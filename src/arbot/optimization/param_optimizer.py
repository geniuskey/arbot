"""Strategy parameter optimizer via grid search and Bayesian optimization.

Provides two optimization methods for finding the best strategy parameters:
grid search (exhaustive) and Bayesian optimization (efficient).
"""

from __future__ import annotations

import itertools
import time
from typing import TYPE_CHECKING, Any

import numpy as np
from pydantic import BaseModel, Field
from scipy.optimize import minimize

from arbot.logging import get_logger

if TYPE_CHECKING:
    from arbot.backtest.engine import BacktestEngine
    from arbot.models.orderbook import OrderBook

logger = get_logger(__name__)


class ParamScore(BaseModel):
    """Result for a single parameter combination.

    Attributes:
        params: Parameter name to value mapping.
        score: Objective function value.
        total_pnl: Total PnL from the backtest.
        sharpe_ratio: Sharpe ratio from the backtest.
        win_rate: Win rate from the backtest.
        max_drawdown_pct: Maximum drawdown percentage.
        total_trades: Number of trades executed.
    """

    params: dict[str, float]
    score: float
    total_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0


class OptimizationResult(BaseModel):
    """Result of a parameter optimization run.

    Attributes:
        best_params: Parameter values that achieved the best score.
        best_score: Best objective function value.
        all_results: All evaluated parameter combinations sorted by
            score descending.
        optimization_time_seconds: Total wall-clock time for the run.
    """

    best_params: dict[str, float]
    best_score: float
    all_results: list[ParamScore] = Field(default_factory=list)
    optimization_time_seconds: float = 0.0


class ParamOptimizer:
    """Optimizes strategy parameters using grid search or Bayesian optimization.

    Evaluates different parameter combinations by running backtests
    and scoring the results against a configurable objective function.

    Attributes:
        objective: Metric to maximize. One of "sharpe_ratio",
            "total_pnl", or "win_rate".
        max_drawdown_constraint: Maximum allowed drawdown percentage.
            Combinations exceeding this are penalized.
    """

    VALID_OBJECTIVES = {"sharpe_ratio", "total_pnl", "win_rate"}

    def __init__(
        self,
        objective: str = "sharpe_ratio",
        max_drawdown_constraint: float = 10.0,
    ) -> None:
        """Initialize the parameter optimizer.

        Args:
            objective: Metric to maximize.
            max_drawdown_constraint: Maximum allowed drawdown percentage.
                Results exceeding this are penalized.

        Raises:
            ValueError: If objective is not a valid metric name.
        """
        if objective not in self.VALID_OBJECTIVES:
            raise ValueError(
                f"Invalid objective '{objective}'. "
                f"Must be one of {self.VALID_OBJECTIVES}"
            )
        self.objective = objective
        self.max_drawdown_constraint = max_drawdown_constraint

    def grid_search(
        self,
        tick_data: list[dict[str, OrderBook]],
        param_grid: dict[str, list[float]],
        pipeline_factory: Any = None,
        engine_factory: type[BacktestEngine] | None = None,
    ) -> OptimizationResult:
        """Run exhaustive grid search over parameter combinations.

        Args:
            tick_data: Historical tick data for backtesting.
            param_grid: Mapping of parameter names to lists of values.
            pipeline_factory: Callable(params: dict) -> ArbitragePipeline.
                Creates a pipeline configured with the given parameters.
            engine_factory: Optional custom BacktestEngine class.

        Returns:
            OptimizationResult with the best parameters and all results.
        """
        start_time = time.monotonic()
        param_names = list(param_grid.keys())
        param_values = [param_grid[name] for name in param_names]
        combinations = list(itertools.product(*param_values))
        total = len(combinations)

        logger.info(
            "grid_search_started",
            objective=self.objective,
            total_combinations=total,
            param_names=param_names,
        )

        all_results: list[ParamScore] = []

        for idx, combo in enumerate(combinations):
            params = dict(zip(param_names, combo))
            result = self._evaluate(
                tick_data, params, pipeline_factory, engine_factory
            )
            all_results.append(result)

            if total > 10 and (idx + 1) % max(1, total // 10) == 0:
                logger.info(
                    "grid_search_progress",
                    completed=idx + 1,
                    total=total,
                    best_so_far=max(r.score for r in all_results),
                )

        all_results.sort(key=lambda r: r.score, reverse=True)
        elapsed = time.monotonic() - start_time

        best = all_results[0] if all_results else ParamScore(params={}, score=0.0)

        logger.info(
            "grid_search_completed",
            best_score=best.score,
            best_params=best.params,
            total_combinations=total,
            elapsed_seconds=elapsed,
        )

        return OptimizationResult(
            best_params=best.params,
            best_score=best.score,
            all_results=all_results,
            optimization_time_seconds=elapsed,
        )

    def bayesian_optimize(
        self,
        tick_data: list[dict[str, OrderBook]],
        param_bounds: dict[str, tuple[float, float]],
        n_iter: int = 20,
        pipeline_factory: Any = None,
        engine_factory: type[BacktestEngine] | None = None,
    ) -> OptimizationResult:
        """Run Bayesian optimization using scipy.optimize.minimize.

        Uses the Nelder-Mead method to explore the parameter space
        efficiently without requiring gradient information.

        Args:
            tick_data: Historical tick data for backtesting.
            param_bounds: Mapping of parameter names to (min, max) bounds.
            n_iter: Maximum number of function evaluations.
            pipeline_factory: Callable(params: dict) -> ArbitragePipeline.
            engine_factory: Optional custom BacktestEngine class.

        Returns:
            OptimizationResult with the best parameters found.
        """
        start_time = time.monotonic()
        param_names = list(param_bounds.keys())
        bounds = [param_bounds[name] for name in param_names]

        logger.info(
            "bayesian_optimize_started",
            objective=self.objective,
            max_iterations=n_iter,
            param_names=param_names,
        )

        all_results: list[ParamScore] = []

        def objective_fn(x: np.ndarray) -> float:
            params = dict(zip(param_names, x.tolist()))
            result = self._evaluate(
                tick_data, params, pipeline_factory, engine_factory
            )
            all_results.append(result)
            # scipy minimizes, so negate for maximization
            return -result.score

        # Start from the midpoint of each bound
        x0 = np.array([(lo + hi) / 2 for lo, hi in bounds])

        scipy_result = minimize(
            objective_fn,
            x0,
            method="Nelder-Mead",
            options={"maxfev": n_iter, "adaptive": True},
        )

        all_results.sort(key=lambda r: r.score, reverse=True)
        elapsed = time.monotonic() - start_time

        best = all_results[0] if all_results else ParamScore(params={}, score=0.0)

        logger.info(
            "bayesian_optimize_completed",
            best_score=best.score,
            best_params=best.params,
            evaluations=len(all_results),
            elapsed_seconds=elapsed,
            scipy_success=scipy_result.success,
        )

        return OptimizationResult(
            best_params=best.params,
            best_score=best.score,
            all_results=all_results,
            optimization_time_seconds=elapsed,
        )

    def _evaluate(
        self,
        tick_data: list[dict[str, OrderBook]],
        params: dict[str, float],
        pipeline_factory: Any,
        engine_factory: type[BacktestEngine] | None,
    ) -> ParamScore:
        """Evaluate a single parameter combination.

        Args:
            tick_data: Historical tick data.
            params: Parameter name to value mapping.
            pipeline_factory: Pipeline factory callable.
            engine_factory: Optional custom BacktestEngine class.

        Returns:
            ParamScore with the evaluation results.
        """
        from arbot.backtest.engine import BacktestEngine

        pipeline = pipeline_factory(params)
        factory = engine_factory or BacktestEngine
        engine = factory(pipeline=pipeline)
        result = engine.run(tick_data)

        score = getattr(result, self.objective, 0.0)

        # Penalize if drawdown exceeds constraint
        if result.max_drawdown_pct > self.max_drawdown_constraint:
            penalty = (result.max_drawdown_pct - self.max_drawdown_constraint) * 0.5
            score = score - penalty

        return ParamScore(
            params=params,
            score=score,
            total_pnl=result.total_pnl,
            sharpe_ratio=result.sharpe_ratio,
            win_rate=result.win_rate,
            max_drawdown_pct=result.max_drawdown_pct,
            total_trades=result.total_trades,
        )
