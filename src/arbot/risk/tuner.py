"""Risk parameter tuning via grid search.

Uses the backtesting engine to evaluate different risk parameter
combinations and find the best configuration for a given objective.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from arbot.logging import get_logger
from arbot.models.config import RiskConfig, TradingFee
from arbot.models.orderbook import OrderBook

if TYPE_CHECKING:
    from arbot.backtest.engine import BacktestEngine

logger = get_logger(__name__)


@dataclass
class TuningResult:
    """Results from a grid search tuning run.

    Attributes:
        best_params: Parameter values that achieved the best score.
        best_score: Best objective score achieved.
        all_results: All results sorted by score descending.
        objective: Name of the objective metric optimized.
        total_combinations: Total number of parameter combinations tested.
    """

    best_params: dict[str, float]
    best_score: float
    all_results: list[dict] = field(default_factory=list)
    objective: str = "sharpe_ratio"
    total_combinations: int = 0


class RiskTuner:
    """Grid search over risk parameters using backtesting.

    Iterates over all combinations of provided parameter values,
    runs a backtest for each, and selects the best based on a
    configurable objective metric.

    Attributes:
        objective: The metric to optimize. One of "sharpe_ratio",
            "total_pnl", or "win_rate".
    """

    VALID_OBJECTIVES = {"sharpe_ratio", "total_pnl", "win_rate"}

    def __init__(self, objective: str = "sharpe_ratio") -> None:
        """Initialize the risk tuner.

        Args:
            objective: Metric to optimize. Must be one of "sharpe_ratio",
                "total_pnl", or "win_rate".

        Raises:
            ValueError: If objective is not a valid metric name.
        """
        if objective not in self.VALID_OBJECTIVES:
            raise ValueError(
                f"Invalid objective '{objective}'. Must be one of {self.VALID_OBJECTIVES}"
            )
        self.objective = objective

    def tune(
        self,
        tick_data: list[dict[str, OrderBook]],
        param_grid: dict[str, list[float]],
        base_config: RiskConfig | None = None,
        engine_factory: type[BacktestEngine] | None = None,
    ) -> TuningResult:
        """Run grid search over parameter combinations.

        Args:
            tick_data: Historical tick data for backtesting.
            param_grid: Mapping of RiskConfig field names to lists of
                values to try.
            base_config: Base configuration to start from. Parameters
                not in param_grid retain their base values.
            engine_factory: Optional custom BacktestEngine class for testing.

        Returns:
            TuningResult with the best parameters and all results.
        """
        base = base_config or RiskConfig()
        param_names = list(param_grid.keys())
        param_values = [param_grid[name] for name in param_names]
        combinations = list(itertools.product(*param_values))
        total = len(combinations)

        logger.info(
            "tuning_started",
            objective=self.objective,
            total_combinations=total,
            param_names=param_names,
        )

        all_results: list[dict] = []

        for idx, combo in enumerate(combinations):
            params = dict(zip(param_names, combo))
            config = base.model_copy(update=params)

            result = self._run_single(tick_data, config, engine_factory)
            result["params"] = params
            all_results.append(result)

            if (idx + 1) % max(1, total // 10) == 0:
                logger.info(
                    "tuning_progress",
                    completed=idx + 1,
                    total=total,
                )

        # Sort by objective score descending
        all_results.sort(key=lambda r: r.get("score", float("-inf")), reverse=True)

        best = all_results[0] if all_results else {"params": {}, "score": 0.0}

        logger.info(
            "tuning_completed",
            best_score=best.get("score", 0.0),
            best_params=best.get("params", {}),
            total_combinations=total,
        )

        return TuningResult(
            best_params=best.get("params", {}),
            best_score=best.get("score", 0.0),
            all_results=all_results,
            objective=self.objective,
            total_combinations=total,
        )

    def _run_single(
        self,
        tick_data: list[dict[str, OrderBook]],
        config: RiskConfig,
        engine_factory: type[BacktestEngine] | None = None,
    ) -> dict:
        """Run a single backtest with the given config.

        Args:
            tick_data: Historical tick data.
            config: Risk configuration for this run.
            engine_factory: Optional custom BacktestEngine class.

        Returns:
            Dict with score and backtest result metrics.
        """
        from arbot.detector.spatial import SpatialDetector
        from arbot.execution.paper_executor import PaperExecutor
        from arbot.risk.manager import RiskManager

        risk_manager = RiskManager(config=config)
        detector = SpatialDetector(
            min_spread_pct=config.max_spread_pct * 0.05,
        )
        executor = PaperExecutor(
            initial_balances={
                "binance": {"USDT": 50000.0, "BTC": 0.0, "ETH": 0.0},
                "okx": {"USDT": 50000.0, "BTC": 0.0, "ETH": 0.0},
            },
            exchange_fees={
                "binance": TradingFee(maker_pct=0.1, taker_pct=0.1),
                "okx": TradingFee(maker_pct=0.1, taker_pct=0.1),
            },
        )

        from arbot.core.pipeline import ArbitragePipeline

        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=risk_manager,
            spatial_detector=detector,
        )

        if engine_factory is not None:
            factory = engine_factory
        else:
            from arbot.backtest.engine import BacktestEngine
            factory = BacktestEngine
        engine = factory(pipeline=pipeline)
        result = engine.run(tick_data)

        score = getattr(result, self.objective, 0.0)

        return {
            "score": score,
            "total_pnl": result.total_pnl,
            "sharpe_ratio": result.sharpe_ratio,
            "win_rate": result.win_rate,
            "max_drawdown_pct": result.max_drawdown_pct,
            "total_trades": result.total_trades,
        }
