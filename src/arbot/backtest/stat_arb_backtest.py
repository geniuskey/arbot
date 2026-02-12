"""Walk-forward backtesting for statistical arbitrage.

Implements a walk-forward optimization approach: trains on historical
windows to find cointegrated pairs and hedge ratios, then tests on
subsequent out-of-sample windows using Z-Score trading signals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from arbot.detector.cointegration import CointegrationAnalyzer
from arbot.detector.pair_scanner import CointegratedPair, PairScanner
from arbot.detector.zscore import ZScoreGenerator, ZScoreSignal
from arbot.logging import get_logger

logger = get_logger(__name__)


@dataclass
class StatArbBacktestResult:
    """Results from a walk-forward statistical arbitrage backtest.

    Attributes:
        total_pnl: Total profit and loss.
        total_trades: Total number of round-trip trades.
        win_rate: Fraction of profitable trades (0 to 1).
        sharpe_ratio: Annualized Sharpe ratio.
        max_drawdown_pct: Maximum drawdown as percentage.
        pair_results: Per-pair breakdown of results.
        walk_forward_windows: Number of walk-forward windows executed.
    """

    total_pnl: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    pair_results: dict[str, dict] = field(default_factory=dict)
    walk_forward_windows: int = 0


class StatArbBacktester:
    """Walk-forward backtester for statistical arbitrage strategies.

    Args:
        train_window: Number of periods for the training window.
        test_window: Number of periods for the test window.
        z_entry: Z-Score threshold for entry.
        z_exit: Z-Score threshold for exit.
        significance_level: P-value threshold for cointegration.
    """

    def __init__(
        self,
        train_window: int = 252,
        test_window: int = 63,
        z_entry: float = 2.0,
        z_exit: float = 0.5,
        significance_level: float = 0.05,
    ) -> None:
        self.train_window = train_window
        self.test_window = test_window
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.significance_level = significance_level

        self._scanner = PairScanner(
            significance_level=significance_level,
            min_half_life=1.0,
            max_half_life=train_window * 2.0,
        )
        self._zscore_gen = ZScoreGenerator(
            entry_threshold=z_entry,
            exit_threshold=z_exit,
        )

    def run(self, price_data: dict[str, np.ndarray]) -> StatArbBacktestResult:
        """Run walk-forward backtest.

        1. Split into train/test windows.
        2. Train: find cointegrated pairs, compute hedge ratios.
        3. Test: trade based on Z-Score signals.
        4. Slide window and repeat.

        Args:
            price_data: Mapping of symbol to price series array.
                All arrays must have the same length.

        Returns:
            StatArbBacktestResult with aggregated metrics.
        """
        symbols = list(price_data.keys())
        if len(symbols) < 2:
            return StatArbBacktestResult()

        lengths = [len(price_data[s]) for s in symbols]
        total_len = min(lengths)

        if total_len < self.train_window + self.test_window:
            return StatArbBacktestResult()

        all_pnls: list[float] = []
        pair_pnls: dict[str, list[float]] = {}
        window_count = 0

        start = 0
        while start + self.train_window + self.test_window <= total_len:
            train_end = start + self.train_window
            test_end = min(train_end + self.test_window, total_len)

            # Training phase: find cointegrated pairs
            train_data = {
                s: price_data[s][start:train_end] for s in symbols
            }
            pairs = self._scanner.scan(train_data, p_threshold=self.significance_level)

            # Testing phase: trade each cointegrated pair
            for pair in pairs:
                pair_key = f"{pair.symbol_a}|{pair.symbol_b}"
                pnls = self._trade_pair(
                    pair,
                    price_data[pair.symbol_a][start:test_end],
                    price_data[pair.symbol_b][start:test_end],
                    train_end - start,
                )
                all_pnls.extend(pnls)
                pair_pnls.setdefault(pair_key, []).extend(pnls)

            window_count += 1
            start += self.test_window

        return self._build_result(all_pnls, pair_pnls, window_count)

    def _trade_pair(
        self,
        pair: CointegratedPair,
        prices_a: np.ndarray,
        prices_b: np.ndarray,
        train_size: int,
    ) -> list[float]:
        """Simulate trading a cointegrated pair during the test window.

        Args:
            pair: Cointegrated pair with hedge ratio.
            prices_a: Full price series for symbol A (train + test).
            prices_b: Full price series for symbol B (train + test).
            train_size: Number of observations in the training window.

        Returns:
            List of per-trade PnL values.
        """
        pnls: list[float] = []
        position: str | None = None  # "long" or "short"
        entry_spread: float = 0.0

        for i in range(train_size, len(prices_a)):
            # Use all data up to current point for Z-Score
            lookback = min(i, self.train_window)
            result = self._zscore_gen.compute(
                prices_a[: i + 1],
                prices_b[: i + 1],
                pair.hedge_ratio,
                lookback,
            )

            current_spread = result.spread

            if position is None:
                if result.signal == ZScoreSignal.ENTRY_LONG:
                    position = "long"
                    entry_spread = current_spread
                elif result.signal == ZScoreSignal.ENTRY_SHORT:
                    position = "short"
                    entry_spread = current_spread
            else:
                if result.signal == ZScoreSignal.EXIT:
                    if position == "long":
                        pnl = current_spread - entry_spread
                    else:
                        pnl = entry_spread - current_spread
                    pnls.append(pnl)
                    position = None

        # Close any open position at end
        if position is not None:
            spread = prices_a[-1] - pair.hedge_ratio * prices_b[-1]
            if position == "long":
                pnl = spread - entry_spread
            else:
                pnl = entry_spread - spread
            pnls.append(pnl)

        return pnls

    def _build_result(
        self,
        all_pnls: list[float],
        pair_pnls: dict[str, list[float]],
        window_count: int,
    ) -> StatArbBacktestResult:
        """Build the backtest result from collected PnL data.

        Args:
            all_pnls: All individual trade PnLs.
            pair_pnls: Per-pair trade PnLs.
            window_count: Number of walk-forward windows.

        Returns:
            StatArbBacktestResult with computed metrics.
        """
        total_trades = len(all_pnls)
        if total_trades == 0:
            return StatArbBacktestResult(walk_forward_windows=window_count)

        total_pnl = sum(all_pnls)
        wins = sum(1 for p in all_pnls if p > 0)
        win_rate = wins / total_trades

        # Sharpe ratio (annualized, assuming daily trades)
        mean_pnl = np.mean(all_pnls)
        std_pnl = np.std(all_pnls, ddof=1) if total_trades > 1 else 0.0
        sharpe_ratio = float(mean_pnl / std_pnl * math.sqrt(252)) if std_pnl > 0 else 0.0

        # Max drawdown
        cumulative = np.cumsum(all_pnls)
        peak = np.maximum.accumulate(cumulative)
        # Avoid division by zero: use absolute drawdown if peak is non-positive
        drawdown = np.where(peak > 0, (peak - cumulative) / peak * 100, 0.0)
        max_drawdown_pct = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

        # Per-pair results
        pair_results: dict[str, dict] = {}
        for pair_key, pnls in pair_pnls.items():
            pair_results[pair_key] = {
                "total_pnl": sum(pnls),
                "trades": len(pnls),
                "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0,
            }

        return StatArbBacktestResult(
            total_pnl=total_pnl,
            total_trades=total_trades,
            win_rate=win_rate,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            pair_results=pair_results,
            walk_forward_windows=window_count,
        )
