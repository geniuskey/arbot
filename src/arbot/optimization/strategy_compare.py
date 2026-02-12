"""Multi-strategy comparison and ranking.

Runs multiple strategies on the same dataset and generates a comparison
report with per-metric rankings to help identify the best overall strategy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from arbot.logging import get_logger

if TYPE_CHECKING:
    from arbot.backtest.engine import BacktestEngine
    from arbot.backtest.metrics import BacktestResult
    from arbot.models.orderbook import OrderBook

logger = get_logger(__name__)


class StrategyConfig(BaseModel):
    """Configuration for a strategy to compare.

    Attributes:
        name: Human-readable strategy name.
        params: Strategy-specific parameters.
        pipeline_factory: Not serializable; set at runtime via the
            compare method's pipeline_factories argument.
    """

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class StrategyResult(BaseModel):
    """Backtest result for a single strategy.

    Attributes:
        strategy_name: Name of the strategy.
        params: Parameters used for the strategy.
        total_pnl: Total PnL from the backtest.
        sharpe_ratio: Sharpe ratio.
        max_drawdown_pct: Maximum drawdown percentage.
        win_rate: Win rate (0 to 1).
        total_trades: Number of trades executed.
        profit_factor: Ratio of gross profit to gross loss.
        avg_profit_per_trade: Average PnL per trade.
    """

    strategy_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    total_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0
    avg_profit_per_trade: float = 0.0


class ComparisonReport(BaseModel):
    """Report comparing multiple strategies.

    Attributes:
        results: Per-strategy results.
        rankings: Mapping of metric name to ordered list of strategy
            names (best first).
        best_overall: Name of the strategy with the best average rank.
    """

    results: list[StrategyResult] = Field(default_factory=list)
    rankings: dict[str, list[str]] = Field(default_factory=dict)
    best_overall: str = ""


class StrategyComparator:
    """Compares multiple strategies on the same dataset.

    Runs each strategy through a backtest and ranks them on multiple
    metrics to produce a comprehensive comparison report.
    """

    RANKING_METRICS = [
        "total_pnl",
        "sharpe_ratio",
        "win_rate",
        "profit_factor",
        "total_trades",
    ]

    # Metrics where lower is better
    LOWER_IS_BETTER = {"max_drawdown_pct"}

    def compare(
        self,
        strategies: list[StrategyConfig],
        tick_data: list[dict[str, OrderBook]],
        pipeline_factories: dict[str, Any],
        engine_factory: type[BacktestEngine] | None = None,
    ) -> ComparisonReport:
        """Run and compare multiple strategies.

        Args:
            strategies: List of strategy configurations.
            tick_data: Historical tick data for backtesting.
            pipeline_factories: Mapping of strategy name to callable
                that accepts params dict and returns an ArbitragePipeline.
            engine_factory: Optional custom BacktestEngine class.

        Returns:
            ComparisonReport with results, rankings, and best overall.
        """
        from arbot.backtest.engine import BacktestEngine

        logger.info(
            "strategy_comparison_started",
            num_strategies=len(strategies),
            num_ticks=len(tick_data),
        )

        results: list[StrategyResult] = []

        for strategy in strategies:
            factory_fn = pipeline_factories.get(strategy.name)
            if factory_fn is None:
                logger.warning(
                    "strategy_factory_missing",
                    strategy_name=strategy.name,
                )
                continue

            pipeline = factory_fn(strategy.params)
            factory = engine_factory or BacktestEngine
            engine = factory(pipeline=pipeline)
            bt_result = engine.run(tick_data)

            sr = StrategyResult(
                strategy_name=strategy.name,
                params=strategy.params,
                total_pnl=bt_result.total_pnl,
                sharpe_ratio=bt_result.sharpe_ratio,
                max_drawdown_pct=bt_result.max_drawdown_pct,
                win_rate=bt_result.win_rate,
                total_trades=bt_result.total_trades,
                profit_factor=bt_result.profit_factor,
                avg_profit_per_trade=bt_result.avg_profit_per_trade,
            )
            results.append(sr)

            logger.info(
                "strategy_evaluated",
                strategy_name=strategy.name,
                total_pnl=bt_result.total_pnl,
                sharpe_ratio=bt_result.sharpe_ratio,
            )

        # Build rankings
        rankings = self._build_rankings(results)
        best_overall = self._find_best_overall(results, rankings)

        logger.info(
            "strategy_comparison_completed",
            best_overall=best_overall,
            num_strategies=len(results),
        )

        return ComparisonReport(
            results=results,
            rankings=rankings,
            best_overall=best_overall,
        )

    def _build_rankings(
        self, results: list[StrategyResult]
    ) -> dict[str, list[str]]:
        """Build per-metric rankings from results.

        Args:
            results: List of strategy results.

        Returns:
            Dict mapping metric name to ordered list of strategy names.
        """
        rankings: dict[str, list[str]] = {}

        all_metrics = self.RANKING_METRICS + ["max_drawdown_pct"]

        for metric in all_metrics:
            reverse = metric not in self.LOWER_IS_BETTER
            sorted_results = sorted(
                results,
                key=lambda r: getattr(r, metric, 0.0),
                reverse=reverse,
            )
            rankings[metric] = [r.strategy_name for r in sorted_results]

        return rankings

    def _find_best_overall(
        self,
        results: list[StrategyResult],
        rankings: dict[str, list[str]],
    ) -> str:
        """Find the strategy with the best average rank.

        Args:
            results: Strategy results.
            rankings: Per-metric rankings.

        Returns:
            Name of the best overall strategy, or empty string if
            no results.
        """
        if not results:
            return ""

        strategy_names = [r.strategy_name for r in results]
        avg_ranks: dict[str, float] = {name: 0.0 for name in strategy_names}

        for metric, ranked_names in rankings.items():
            for rank_idx, name in enumerate(ranked_names):
                avg_ranks[name] += rank_idx

        num_metrics = len(rankings) if rankings else 1
        for name in avg_ranks:
            avg_ranks[name] /= num_metrics

        return min(avg_ranks, key=lambda n: avg_ranks[n])
