"""Cointegration analysis for statistical arbitrage.

Provides Engle-Granger and Johansen cointegration testing and mean-reversion
half-life estimation for identifying statistically linked asset pairs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from statsmodels.regression.linear_model import OLS
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen
from statsmodels.tools.tools import add_constant

from arbot.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CointegrationResult:
    """Result of a cointegration test between two price series.

    Attributes:
        is_cointegrated: Whether the pair is cointegrated at the given significance.
        p_value: P-value from the cointegration test.
        hedge_ratio: OLS hedge ratio (beta) from regressing series_a on series_b.
        test_statistic: ADF test statistic on the spread residuals.
        half_life: Mean-reversion half-life in periods.
    """

    is_cointegrated: bool
    p_value: float
    hedge_ratio: float
    test_statistic: float
    half_life: float


@dataclass(frozen=True)
class JohansenResult:
    """Result of the Johansen multivariate cointegration test.

    Attributes:
        num_cointegrating_vectors: Number of cointegrating relationships found.
        trace_statistics: Trace test statistics for each rank.
        critical_values_95: 95% critical values for the trace test.
        eigenvectors: Cointegrating vectors (columns of the eigenvector matrix).
        eigenvalues: Eigenvalues from the Johansen test.
    """

    num_cointegrating_vectors: int
    trace_statistics: list[float]
    critical_values_95: list[float]
    eigenvectors: list[list[float]]
    eigenvalues: list[float]


class CointegrationAnalyzer:
    """Analyzes cointegration relationships between price series.

    Args:
        significance_level: P-value threshold for cointegration.
    """

    def __init__(self, significance_level: float = 0.05) -> None:
        self.significance_level = significance_level

    def test_engle_granger(
        self, series_a: np.ndarray, series_b: np.ndarray
    ) -> CointegrationResult:
        """Run Engle-Granger cointegration test.

        Args:
            series_a: First price series.
            series_b: Second price series.

        Returns:
            CointegrationResult with test outcomes and hedge ratio.
        """
        if len(series_a) < 20 or len(series_b) < 20:
            return CointegrationResult(
                is_cointegrated=False,
                p_value=1.0,
                hedge_ratio=0.0,
                test_statistic=0.0,
                half_life=float("inf"),
            )

        # OLS regression: series_a = beta * series_b + alpha + residual
        series_b_const = add_constant(series_b)
        model = OLS(series_a, series_b_const).fit()
        hedge_ratio = float(model.params[1])
        spread = series_a - hedge_ratio * series_b

        # Engle-Granger cointegration test
        test_stat, p_value, _ = coint(series_a, series_b)
        test_stat = float(test_stat)
        p_value = float(p_value)

        # Compute half-life
        half_life = self.compute_half_life(spread)

        is_cointegrated = p_value < self.significance_level

        return CointegrationResult(
            is_cointegrated=is_cointegrated,
            p_value=p_value,
            hedge_ratio=hedge_ratio,
            test_statistic=test_stat,
            half_life=half_life,
        )

    def test_johansen(
        self, series: list[np.ndarray], det_order: int = 0, k_ar_diff: int = 1
    ) -> JohansenResult:
        """Run Johansen multivariate cointegration test.

        Args:
            series: List of price series arrays (must all have the same length).
            det_order: Deterministic term order (-1=no constant, 0=constant, 1=trend).
            k_ar_diff: Number of lagged differences in the VECM model.

        Returns:
            JohansenResult with trace statistics, critical values, and eigenvectors.
        """
        min_len = min(len(s) for s in series)
        if len(series) < 2 or min_len < 20:
            n = len(series)
            return JohansenResult(
                num_cointegrating_vectors=0,
                trace_statistics=[0.0] * n,
                critical_values_95=[0.0] * n,
                eigenvectors=[[0.0] * n for _ in range(n)],
                eigenvalues=[0.0] * n,
            )

        # Stack series into a matrix (observations x variables)
        data = np.column_stack([s[:min_len] for s in series])

        result = coint_johansen(data, det_order, k_ar_diff)

        # Trace statistics and 95% critical values (column index 1 = 95%)
        trace_stats = result.lr1.tolist()
        crit_95 = result.cvt[:, 1].tolist()

        # Count cointegrating vectors: where trace stat > critical value
        num_coint = sum(
            1 for ts, cv in zip(trace_stats, crit_95) if ts > cv
        )

        eigenvectors = result.evec.tolist()
        eigenvalues = result.eig.tolist()

        return JohansenResult(
            num_cointegrating_vectors=num_coint,
            trace_statistics=trace_stats,
            critical_values_95=crit_95,
            eigenvectors=eigenvectors,
            eigenvalues=eigenvalues,
        )

    @staticmethod
    def compute_half_life(spread: np.ndarray) -> float:
        """Compute mean-reversion half-life from AR(1) model.

        Fits spread[t] = phi * spread[t-1] + epsilon and computes
        half_life = -log(2) / log(phi).

        Args:
            spread: Spread time series.

        Returns:
            Half-life in periods. Returns inf if non-mean-reverting.
        """
        if len(spread) < 3:
            return float("inf")

        lag = spread[:-1].reshape(-1, 1)
        lag_const = add_constant(lag)
        current = spread[1:]

        model = OLS(current, lag_const).fit()
        phi = float(model.params[1])

        if phi <= 0 or phi >= 1:
            return float("inf")

        half_life = -np.log(2) / np.log(phi)
        return float(half_life)
