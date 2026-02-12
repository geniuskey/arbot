"""Automated cointegrated pair discovery.

Scans all combinations of price series to find statistically
cointegrated pairs suitable for statistical arbitrage.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
from pydantic import BaseModel

from arbot.detector.cointegration import CointegrationAnalyzer
from arbot.logging import get_logger

logger = get_logger(__name__)


class CointegratedPair(BaseModel):
    """A discovered cointegrated pair.

    Attributes:
        symbol_a: First symbol identifier.
        symbol_b: Second symbol identifier.
        p_value: Cointegration test p-value.
        hedge_ratio: OLS hedge ratio.
        half_life: Mean-reversion half-life in periods.
    """

    model_config = {"frozen": True}

    symbol_a: str
    symbol_b: str
    p_value: float
    hedge_ratio: float
    half_life: float


class PairScanner:
    """Scans price series combinations for cointegrated pairs.

    Args:
        significance_level: P-value threshold for cointegration.
        min_half_life: Minimum acceptable half-life in periods.
        max_half_life: Maximum acceptable half-life in periods.
    """

    def __init__(
        self,
        significance_level: float = 0.05,
        min_half_life: float = 1.0,
        max_half_life: float = 100.0,
    ) -> None:
        self.significance_level = significance_level
        self.min_half_life = min_half_life
        self.max_half_life = max_half_life
        self._analyzer = CointegrationAnalyzer(significance_level=significance_level)

    def scan(
        self,
        price_data: dict[str, np.ndarray],
        p_threshold: float = 0.05,
    ) -> list[CointegratedPair]:
        """Scan all pair combinations and return cointegrated pairs.

        Args:
            price_data: Mapping of symbol name to price series array.
            p_threshold: Maximum p-value to include a pair.

        Returns:
            List of CointegratedPair sorted by p-value ascending (most significant first).
        """
        symbols = list(price_data.keys())
        pairs: list[CointegratedPair] = []

        for sym_a, sym_b in combinations(symbols, 2):
            series_a = price_data[sym_a]
            series_b = price_data[sym_b]

            result = self._analyzer.test_engle_granger(series_a, series_b)

            if not result.is_cointegrated:
                continue

            if result.p_value >= p_threshold:
                continue

            if result.half_life < self.min_half_life or result.half_life > self.max_half_life:
                continue

            pairs.append(
                CointegratedPair(
                    symbol_a=sym_a,
                    symbol_b=sym_b,
                    p_value=result.p_value,
                    hedge_ratio=result.hedge_ratio,
                    half_life=result.half_life,
                )
            )

        pairs.sort(key=lambda p: p.p_value)
        return pairs
