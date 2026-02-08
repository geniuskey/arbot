"""Backtesting engine for running arbitrage strategies on historical data."""

from __future__ import annotations

from arbot.backtest.metrics import BacktestMetrics, BacktestResult
from arbot.core.pipeline import ArbitragePipeline
from arbot.logging import get_logger
from arbot.models.orderbook import OrderBook

logger = get_logger(__name__)


class BacktestEngine:
    """Runs a backtest by feeding tick data through the arbitrage pipeline.

    Iterates over historical (or synthetic) order book snapshots, executing
    the full detection-risk-execution pipeline on each tick and collecting
    PnL results for metric calculation.

    Attributes:
        pipeline: The arbitrage pipeline to run on each tick.
    """

    def __init__(self, pipeline: ArbitragePipeline) -> None:
        """Initialize the backtest engine.

        Args:
            pipeline: Configured ArbitragePipeline instance.
        """
        self.pipeline = pipeline

    def run(
        self,
        tick_data: list[dict[str, OrderBook]],
        initial_capital: float = 100_000.0,
    ) -> BacktestResult:
        """Run the backtest over the provided tick data.

        Feeds each tick through the pipeline, collects trade PnL from
        executed trades, and computes performance metrics.

        Args:
            tick_data: List of tick snapshots, each mapping exchange name
                to an OrderBook.
            initial_capital: Starting capital in USD for metric calculations.

        Returns:
            BacktestResult with comprehensive performance metrics.
        """
        total_ticks = len(tick_data)
        trade_pnls: list[float] = []
        last_logged_pct = 0

        logger.info(
            "backtest_started",
            total_ticks=total_ticks,
            initial_capital=initial_capital,
        )

        for idx, orderbooks in enumerate(tick_data):
            results = self.pipeline.run_once(orderbooks)

            for buy_result, sell_result in results:
                pnl = ArbitragePipeline._estimate_trade_pnl(buy_result, sell_result)
                trade_pnls.append(pnl)

            # Progress logging at 10% intervals
            if total_ticks > 0:
                progress_pct = int((idx + 1) / total_ticks * 100)
                if progress_pct >= last_logged_pct + 10:
                    last_logged_pct = progress_pct // 10 * 10
                    logger.info(
                        "backtest_progress",
                        progress_pct=last_logged_pct,
                        ticks_processed=idx + 1,
                        trades_so_far=len(trade_pnls),
                    )

        result = BacktestMetrics.calculate(trade_pnls, initial_capital)

        logger.info(
            "backtest_completed",
            total_ticks=total_ticks,
            total_trades=result.total_trades,
            total_pnl=result.total_pnl,
            win_rate=result.win_rate,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown_pct=result.max_drawdown_pct,
        )

        return result
