"""Paper vs backtest divergence analysis.

Compares paper trading results with backtest results on overlapping
time periods to identify systematic divergences such as slippage,
timing differences, and missed signals.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from arbot.logging import get_logger

logger = get_logger(__name__)


class TradeRecord(BaseModel):
    """Simplified trade record for divergence analysis.

    Attributes:
        timestamp: Unix timestamp of the trade.
        symbol: Trading pair (e.g. "BTC/USDT").
        pnl: Profit or loss in USD.
        buy_exchange: Exchange where the buy was placed.
        sell_exchange: Exchange where the sell was placed.
        spread_pct: Net spread percentage at execution.
    """

    timestamp: float
    symbol: str
    pnl: float
    buy_exchange: str = ""
    sell_exchange: str = ""
    spread_pct: float = 0.0


class DivergenceReport(BaseModel):
    """Report of paper vs backtest divergence analysis.

    Attributes:
        pnl_correlation: Pearson correlation between PnL curves.
            1.0 = perfect correlation, 0.0 = no correlation.
        mean_divergence_pct: Mean absolute divergence between
            per-trade PnLs as a percentage.
        signal_match_rate: Fraction of paper trades that have a
            matching backtest trade (by timestamp and symbol).
        systematic_bias: Average PnL difference (paper - backtest).
            Positive means paper outperforms backtest.
        paper_total_pnl: Total PnL from paper trading.
        backtest_total_pnl: Total PnL from backtesting.
        paper_trade_count: Number of paper trades.
        backtest_trade_count: Number of backtest trades.
        recommendations: List of actionable recommendations.
    """

    pnl_correlation: float = 0.0
    mean_divergence_pct: float = 0.0
    signal_match_rate: float = 0.0
    systematic_bias: float = 0.0
    paper_total_pnl: float = 0.0
    backtest_total_pnl: float = 0.0
    paper_trade_count: int = 0
    backtest_trade_count: int = 0
    recommendations: list[str] = Field(default_factory=list)


class DivergenceAnalyzer:
    """Analyzes divergence between paper trading and backtest results.

    Compares trade-level results to identify systematic differences
    and generate actionable recommendations.

    Attributes:
        timestamp_tolerance_seconds: Maximum time difference for
            matching paper and backtest trades.
    """

    def __init__(self, timestamp_tolerance_seconds: float = 5.0) -> None:
        """Initialize the divergence analyzer.

        Args:
            timestamp_tolerance_seconds: Maximum timestamp difference
                in seconds for two trades to be considered a match.
        """
        self.timestamp_tolerance_seconds = timestamp_tolerance_seconds

    def analyze(
        self,
        paper_trades: list[TradeRecord],
        backtest_trades: list[TradeRecord],
    ) -> DivergenceReport:
        """Analyze divergence between paper and backtest trades.

        Args:
            paper_trades: Trades from paper trading.
            backtest_trades: Trades from backtesting.

        Returns:
            DivergenceReport with correlation, divergence metrics,
            and recommendations.
        """
        logger.info(
            "divergence_analysis_started",
            paper_count=len(paper_trades),
            backtest_count=len(backtest_trades),
        )

        paper_total_pnl = sum(t.pnl for t in paper_trades)
        backtest_total_pnl = sum(t.pnl for t in backtest_trades)

        # Match trades
        matched_pairs = self._match_trades(paper_trades, backtest_trades)
        signal_match_rate = (
            len(matched_pairs) / len(paper_trades)
            if paper_trades
            else 0.0
        )

        # Calculate metrics from matched pairs
        pnl_correlation = self._calculate_correlation(matched_pairs)
        mean_divergence_pct = self._calculate_mean_divergence(matched_pairs)
        systematic_bias = self._calculate_systematic_bias(matched_pairs)

        recommendations = self._generate_recommendations(
            pnl_correlation=pnl_correlation,
            mean_divergence_pct=mean_divergence_pct,
            signal_match_rate=signal_match_rate,
            systematic_bias=systematic_bias,
            paper_count=len(paper_trades),
            backtest_count=len(backtest_trades),
        )

        report = DivergenceReport(
            pnl_correlation=pnl_correlation,
            mean_divergence_pct=mean_divergence_pct,
            signal_match_rate=signal_match_rate,
            systematic_bias=systematic_bias,
            paper_total_pnl=paper_total_pnl,
            backtest_total_pnl=backtest_total_pnl,
            paper_trade_count=len(paper_trades),
            backtest_trade_count=len(backtest_trades),
            recommendations=recommendations,
        )

        logger.info(
            "divergence_analysis_completed",
            pnl_correlation=pnl_correlation,
            signal_match_rate=signal_match_rate,
            systematic_bias=systematic_bias,
        )

        return report

    def _match_trades(
        self,
        paper_trades: list[TradeRecord],
        backtest_trades: list[TradeRecord],
    ) -> list[tuple[TradeRecord, TradeRecord]]:
        """Match paper trades with backtest trades by timestamp and symbol.

        Uses greedy matching: each backtest trade is matched to at most
        one paper trade.

        Args:
            paper_trades: Paper trading records.
            backtest_trades: Backtest trading records.

        Returns:
            List of matched (paper, backtest) trade pairs.
        """
        matched: list[tuple[TradeRecord, TradeRecord]] = []
        used_bt_indices: set[int] = set()

        for pt in paper_trades:
            best_idx: int | None = None
            best_dt = float("inf")

            for i, bt in enumerate(backtest_trades):
                if i in used_bt_indices:
                    continue
                if pt.symbol != bt.symbol:
                    continue
                dt = abs(pt.timestamp - bt.timestamp)
                if dt <= self.timestamp_tolerance_seconds and dt < best_dt:
                    best_dt = dt
                    best_idx = i

            if best_idx is not None:
                matched.append((pt, backtest_trades[best_idx]))
                used_bt_indices.add(best_idx)

        return matched

    def _calculate_correlation(
        self,
        matched_pairs: list[tuple[TradeRecord, TradeRecord]],
    ) -> float:
        """Calculate Pearson correlation between matched PnL values.

        Args:
            matched_pairs: List of (paper, backtest) trade pairs.

        Returns:
            Pearson correlation coefficient, or 0.0 if insufficient data.
        """
        if len(matched_pairs) < 2:
            return 0.0

        paper_pnls = [p.pnl for p, _ in matched_pairs]
        bt_pnls = [b.pnl for _, b in matched_pairs]

        mean_p = sum(paper_pnls) / len(paper_pnls)
        mean_b = sum(bt_pnls) / len(bt_pnls)

        cov = sum(
            (p - mean_p) * (b - mean_b) for p, b in zip(paper_pnls, bt_pnls)
        )
        var_p = sum((p - mean_p) ** 2 for p in paper_pnls)
        var_b = sum((b - mean_b) ** 2 for b in bt_pnls)

        denom = math.sqrt(var_p * var_b)
        if denom == 0:
            return 0.0

        return cov / denom

    def _calculate_mean_divergence(
        self,
        matched_pairs: list[tuple[TradeRecord, TradeRecord]],
    ) -> float:
        """Calculate mean absolute divergence between matched trades.

        Divergence is expressed as a percentage relative to the average
        absolute PnL.

        Args:
            matched_pairs: Matched trade pairs.

        Returns:
            Mean divergence as a percentage, or 0.0 if no matched pairs.
        """
        if not matched_pairs:
            return 0.0

        abs_diffs: list[float] = []
        abs_pnls: list[float] = []

        for paper, bt in matched_pairs:
            abs_diffs.append(abs(paper.pnl - bt.pnl))
            avg_pnl = (abs(paper.pnl) + abs(bt.pnl)) / 2
            abs_pnls.append(avg_pnl)

        mean_abs_pnl = sum(abs_pnls) / len(abs_pnls) if abs_pnls else 0.0
        mean_diff = sum(abs_diffs) / len(abs_diffs)

        if mean_abs_pnl == 0:
            return 0.0

        return (mean_diff / mean_abs_pnl) * 100

    def _calculate_systematic_bias(
        self,
        matched_pairs: list[tuple[TradeRecord, TradeRecord]],
    ) -> float:
        """Calculate systematic bias (paper - backtest) in PnL.

        Args:
            matched_pairs: Matched trade pairs.

        Returns:
            Average PnL difference. Positive means paper outperforms.
        """
        if not matched_pairs:
            return 0.0

        diffs = [paper.pnl - bt.pnl for paper, bt in matched_pairs]
        return sum(diffs) / len(diffs)

    def _generate_recommendations(
        self,
        pnl_correlation: float,
        mean_divergence_pct: float,
        signal_match_rate: float,
        systematic_bias: float,
        paper_count: int,
        backtest_count: int,
    ) -> list[str]:
        """Generate recommendations based on divergence metrics.

        Args:
            pnl_correlation: PnL correlation coefficient.
            mean_divergence_pct: Mean divergence percentage.
            signal_match_rate: Signal match rate.
            systematic_bias: Systematic PnL bias.
            paper_count: Number of paper trades.
            backtest_count: Number of backtest trades.

        Returns:
            List of recommendation strings.
        """
        recs: list[str] = []

        if pnl_correlation < 0.5:
            recs.append(
                "Low PnL correlation (<0.5) suggests the backtest model "
                "does not accurately reflect live conditions. Review "
                "fill simulation and latency modeling."
            )

        if mean_divergence_pct > 20.0:
            recs.append(
                "High mean divergence (>20%) indicates significant "
                "per-trade PnL differences. Check for slippage, "
                "partial fills, or stale order book data."
            )

        if signal_match_rate < 0.7:
            recs.append(
                "Low signal match rate (<70%) means many paper trades "
                "have no corresponding backtest trade. Review timing "
                "alignment and signal detection thresholds."
            )

        if systematic_bias < -1.0:
            recs.append(
                "Negative systematic bias (paper underperforms backtest) "
                "suggests execution costs are higher than modeled. "
                "Increase fee estimates or add slippage modeling."
            )
        elif systematic_bias > 1.0:
            recs.append(
                "Positive systematic bias (paper outperforms backtest) "
                "suggests the backtest is too conservative. Review "
                "fee and slippage assumptions."
            )

        if paper_count > 0 and backtest_count > 0:
            trade_ratio = paper_count / backtest_count
            if trade_ratio < 0.5:
                recs.append(
                    "Paper trading executed significantly fewer trades "
                    "than the backtest. Check for connectivity issues, "
                    "rate limiting, or overly strict live risk checks."
                )
            elif trade_ratio > 2.0:
                recs.append(
                    "Paper trading executed significantly more trades "
                    "than the backtest. The backtest may be missing "
                    "some market conditions or using stale data."
                )

        if not recs:
            recs.append(
                "Paper and backtest results are well aligned. "
                "Continue monitoring for drift."
            )

        return recs
