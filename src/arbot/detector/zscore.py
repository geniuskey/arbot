"""Z-Score based trading signal generation.

Computes the spread between two cointegrated series, normalizes
it into a Z-Score, and generates entry/exit signals based on
configurable thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np


class ZScoreSignal(Enum):
    """Trading signal derived from Z-Score analysis."""

    ENTRY_LONG = "entry_long"
    ENTRY_SHORT = "entry_short"
    EXIT = "exit"
    HOLD = "hold"


@dataclass(frozen=True)
class ZScoreResult:
    """Result of Z-Score computation.

    Attributes:
        zscore: Current Z-Score of the spread.
        spread: Current spread value.
        mean: Rolling mean of the spread.
        std: Rolling standard deviation of the spread.
        signal: Generated trading signal.
    """

    zscore: float
    spread: float
    mean: float
    std: float
    signal: ZScoreSignal


class ZScoreGenerator:
    """Generates Z-Score based trading signals.

    Args:
        entry_threshold: Z-Score magnitude to trigger entry (default 2.0).
        exit_threshold: Z-Score magnitude to trigger exit (default 0.5).
    """

    def __init__(
        self,
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
    ) -> None:
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold

    def compute(
        self,
        prices_a: np.ndarray,
        prices_b: np.ndarray,
        hedge_ratio: float,
        lookback: int = 100,
    ) -> ZScoreResult:
        """Compute Z-Score of spread and generate signal.

        Args:
            prices_a: Price series for first asset.
            prices_b: Price series for second asset.
            hedge_ratio: Hedge ratio from cointegration analysis.
            lookback: Rolling window size for mean/std computation.

        Returns:
            ZScoreResult with Z-Score value and trading signal.
        """
        spread = prices_a - hedge_ratio * prices_b

        # Use the last `lookback` observations for rolling stats
        window = spread[-lookback:]
        mean = float(np.mean(window))
        std = float(np.std(window, ddof=1)) if len(window) > 1 else 0.0

        current_spread = float(spread[-1])

        if std == 0.0:
            return ZScoreResult(
                zscore=0.0,
                spread=current_spread,
                mean=mean,
                std=std,
                signal=ZScoreSignal.HOLD,
            )

        zscore = (current_spread - mean) / std

        signal = self._determine_signal(zscore)

        return ZScoreResult(
            zscore=zscore,
            spread=current_spread,
            mean=mean,
            std=std,
            signal=signal,
        )

    def _determine_signal(self, zscore: float) -> ZScoreSignal:
        """Determine trading signal from Z-Score value.

        Args:
            zscore: Current Z-Score.

        Returns:
            Appropriate ZScoreSignal.
        """
        if zscore < -self.entry_threshold:
            return ZScoreSignal.ENTRY_LONG
        elif zscore > self.entry_threshold:
            return ZScoreSignal.ENTRY_SHORT
        elif abs(zscore) < self.exit_threshold:
            return ZScoreSignal.EXIT
        else:
            return ZScoreSignal.HOLD
