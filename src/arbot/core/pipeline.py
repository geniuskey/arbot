"""Arbitrage pipeline: detection -> risk check -> execution.

Orchestrates the complete arbitrage workflow in a single cycle,
collecting statistics on detection, execution, and rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from arbot.detector.spatial import SpatialDetector
from arbot.detector.statistical import StatisticalDetector
from arbot.detector.triangular import TriangularDetector
from arbot.execution.base import BaseExecutor, InsufficientBalanceError
from arbot.logging import get_logger
from arbot.models.orderbook import OrderBook
from arbot.models.signal import ArbitrageSignal, ArbitrageStrategy, SignalStatus
from arbot.models.trade import TradeResult
from arbot.risk.manager import RiskManager


@dataclass
class PipelineStats:
    """Aggregated pipeline execution statistics.

    Attributes:
        total_signals_detected: Number of signals found by detectors.
        total_signals_approved: Number of signals that passed risk checks.
        total_signals_rejected: Number of signals rejected by risk manager.
        total_signals_executed: Number of signals successfully executed.
        total_signals_failed: Number of signals that failed during execution.
        total_pnl_usd: Cumulative estimated PnL in USD.
        total_fees_usd: Cumulative fees in USD.
        cycles_run: Number of pipeline cycles completed.
        started_at: Timestamp when the pipeline started.
    """

    total_signals_detected: int = 0
    total_signals_approved: int = 0
    total_signals_rejected: int = 0
    total_signals_executed: int = 0
    total_signals_failed: int = 0
    total_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    cycles_run: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ArbitragePipeline:
    """Orchestrates detection -> risk check -> execution flow.

    Supports spatial, triangular, and statistical detectors. In each cycle:
    1. Detectors scan order books for arbitrage signals.
    2. Risk manager validates each signal against risk parameters.
    3. Executor places (simulated or real) trades for approved signals.

    Attributes:
        spatial_detector: Spatial arbitrage detector (optional).
        triangular_detector: Triangular arbitrage detector (optional).
        statistical_detector: Statistical arbitrage detector (optional).
        executor: Trade executor (paper or live).
        risk_manager: Risk manager for signal validation.
    """

    def __init__(
        self,
        executor: BaseExecutor,
        risk_manager: RiskManager,
        spatial_detector: SpatialDetector | None = None,
        triangular_detector: TriangularDetector | None = None,
        statistical_detector: StatisticalDetector | None = None,
    ) -> None:
        """Initialize the pipeline.

        Args:
            executor: Trade executor instance.
            risk_manager: Risk manager instance.
            spatial_detector: Spatial detector (cross-exchange).
            triangular_detector: Triangular detector (single-exchange).
            statistical_detector: Statistical detector (cointegration-based).
        """
        self.spatial_detector = spatial_detector
        self.triangular_detector = triangular_detector
        self.statistical_detector = statistical_detector
        self.executor = executor
        self.risk_manager = risk_manager
        self._stats = PipelineStats()
        self._trade_log: list[tuple[ArbitrageSignal, TradeResult, TradeResult]] = []
        self._logger = get_logger("pipeline")

    def run_once(
        self,
        orderbooks: dict[str, OrderBook],
        triangular_exchange: str | None = None,
        triangular_orderbooks: dict[str, OrderBook] | None = None,
    ) -> list[tuple[TradeResult, TradeResult]]:
        """Run a single detection-risk-execution cycle.

        Args:
            orderbooks: For spatial detection, mapping of exchange name
                to OrderBook (same symbol across exchanges).
                Also used to update executor order books.
            triangular_exchange: Exchange name for triangular detection.
            triangular_orderbooks: For triangular detection, mapping of
                symbol to OrderBook on a single exchange.

        Returns:
            List of (buy_result, sell_result) tuples for executed trades.
        """
        self._stats.cycles_run += 1
        results: list[tuple[TradeResult, TradeResult]] = []

        # Detect signals
        signals: list[ArbitrageSignal] = []

        if self.spatial_detector is not None and orderbooks:
            spatial_signals = self.spatial_detector.detect(orderbooks)
            signals.extend(spatial_signals)

        if (
            self.triangular_detector is not None
            and triangular_exchange is not None
            and triangular_orderbooks is not None
        ):
            tri_signals = self.triangular_detector.detect(
                triangular_orderbooks, triangular_exchange
            )
            signals.extend(tri_signals)

        if self.statistical_detector is not None and orderbooks:
            stat_signals = self.statistical_detector.detect(orderbooks)
            signals.extend(stat_signals)

        self._stats.total_signals_detected += len(signals)

        # Update executor order books
        if hasattr(self.executor, "update_orderbooks"):
            # Build "exchange:symbol" keyed dict for PaperExecutor
            executor_obs: dict[str, OrderBook] = {}
            for exchange_name, ob in orderbooks.items():
                executor_obs[f"{exchange_name}:{ob.symbol}"] = ob
            if triangular_orderbooks:
                for symbol, ob in triangular_orderbooks.items():
                    executor_obs[f"{ob.exchange}:{symbol}"] = ob
            self.executor.update_orderbooks(executor_obs)

        # Risk check and execute
        portfolio = self.executor.get_portfolio()

        rejection_reasons: dict[str, int] = {}
        for signal in signals:
            approved, reason = self.risk_manager.check_signal(signal, portfolio)
            if not approved:
                self._stats.total_signals_rejected += 1
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue

            self._stats.total_signals_approved += 1

            try:
                if signal.strategy == ArbitrageStrategy.TRIANGULAR:
                    tri_results = self.executor.execute_triangular(signal)
                    self._stats.total_signals_executed += 1
                    fees = sum(r.fee for r in tri_results)
                    # PnL: last leg output - first leg input
                    first = tri_results[0]
                    last = tri_results[-1]
                    pnl = (
                        last.filled_quantity * last.filled_price
                        - first.filled_quantity * first.filled_price
                    )
                    self._stats.total_pnl_usd += pnl
                    self._stats.total_fees_usd += fees
                    self.risk_manager.record_trade(pnl)
                    # Store first and last legs as buy/sell pair for compatibility
                    self._trade_log.append((signal, first, last))
                    results.append((first, last))
                    portfolio = self.executor.get_portfolio()
                else:
                    buy_result, sell_result = self.executor.execute(signal)
                    self._stats.total_signals_executed += 1

                    # Calculate PnL from this trade
                    pnl = self._estimate_trade_pnl(buy_result, sell_result)
                    fees = buy_result.fee + sell_result.fee
                    self._stats.total_pnl_usd += pnl
                    self._stats.total_fees_usd += fees

                    # Report to risk manager
                    self.risk_manager.record_trade(pnl)

                    self._trade_log.append((signal, buy_result, sell_result))
                    results.append((buy_result, sell_result))

                    # Refresh portfolio after trade
                    portfolio = self.executor.get_portfolio()

            except (InsufficientBalanceError, ValueError) as e:
                self._stats.total_signals_failed += 1
                self._logger.warning(
                    "signal_execution_failed",
                    symbol=signal.symbol,
                    buy_exchange=signal.buy_exchange,
                    sell_exchange=signal.sell_exchange,
                    error=str(e),
                )

        if rejection_reasons:
            for reason, count in rejection_reasons.items():
                self._stats.rejection_reasons[reason] = (
                    self._stats.rejection_reasons.get(reason, 0) + count
                )
            self._logger.debug(
                "signals_rejected_summary",
                rejections=rejection_reasons,
                total=sum(rejection_reasons.values()),
            )

        return results

    def get_stats(self) -> PipelineStats:
        """Return aggregated pipeline statistics.

        Returns:
            PipelineStats with counts and PnL summary.
        """
        return self._stats

    def get_trade_log(
        self,
    ) -> list[tuple[ArbitrageSignal, TradeResult, TradeResult]]:
        """Return full trade log with signals and results.

        Returns:
            List of (signal, buy_result, sell_result) tuples.
        """
        return list(self._trade_log)

    @staticmethod
    def _estimate_trade_pnl(
        buy_result: TradeResult, sell_result: TradeResult
    ) -> float:
        """Estimate PnL from a buy/sell pair in quote currency.

        Computes the difference between sell proceeds and buy cost,
        minus fees on both sides.

        Args:
            buy_result: The buy trade result.
            sell_result: The sell trade result.

        Returns:
            Estimated PnL in quote currency (USD-equivalent).
        """
        buy_cost = buy_result.filled_quantity * buy_result.filled_price
        sell_proceeds = sell_result.filled_quantity * sell_result.filled_price
        return sell_proceeds - buy_cost
