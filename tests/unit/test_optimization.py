"""Tests for arbot.optimization package.

Tests ParamOptimizer, StrategyComparator, and DivergenceAnalyzer.
"""

from __future__ import annotations

import pytest

from arbot.backtest.metrics import BacktestResult
from arbot.optimization.divergence import DivergenceAnalyzer, DivergenceReport, TradeRecord
from arbot.optimization.param_optimizer import (
    OptimizationResult,
    ParamOptimizer,
    ParamScore,
)
from arbot.optimization.strategy_compare import (
    ComparisonReport,
    StrategyComparator,
    StrategyConfig,
    StrategyResult,
)


# ── Helpers ────────────────────────────────────────────────────────


def _make_backtest_result(
    total_pnl: float = 100.0,
    sharpe_ratio: float = 1.5,
    win_rate: float = 0.6,
    max_drawdown_pct: float = 2.0,
    total_trades: int = 10,
    profit_factor: float = 1.5,
) -> BacktestResult:
    """Create a BacktestResult for testing."""
    return BacktestResult(
        total_pnl=total_pnl,
        total_trades=total_trades,
        win_count=int(total_trades * win_rate),
        loss_count=total_trades - int(total_trades * win_rate),
        win_rate=win_rate,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        profit_factor=profit_factor,
        avg_profit_per_trade=total_pnl / max(total_trades, 1),
        pnl_curve=[total_pnl * (i + 1) / total_trades for i in range(total_trades)],
    )


class MockEngine:
    """Mock backtest engine that returns configurable results."""

    def __init__(self, pipeline: object) -> None:
        self.pipeline = pipeline

    def run(
        self,
        tick_data: list,
        initial_capital: float = 100_000.0,
    ) -> BacktestResult:
        # Access the stored result from the mock pipeline
        if hasattr(self.pipeline, "_mock_result"):
            return self.pipeline._mock_result
        return _make_backtest_result()


class MockPipeline:
    """Mock pipeline with configurable backtest result."""

    def __init__(self, result: BacktestResult | None = None) -> None:
        self._mock_result = result or _make_backtest_result()


# ── ParamOptimizer Tests ───────────────────────────────────────────


class TestParamOptimizer:
    """Tests for ParamOptimizer."""

    def test_invalid_objective_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid objective"):
            ParamOptimizer(objective="invalid")

    def test_valid_objectives(self) -> None:
        for obj in ("sharpe_ratio", "total_pnl", "win_rate"):
            opt = ParamOptimizer(objective=obj)
            assert opt.objective == obj

    def test_grid_search_basic(self) -> None:
        """Grid search should evaluate all combinations."""
        call_count = 0

        def factory(params: dict) -> MockPipeline:
            nonlocal call_count
            call_count += 1
            sharpe = params.get("min_spread_pct", 0.0) * 10
            return MockPipeline(_make_backtest_result(sharpe_ratio=sharpe))

        opt = ParamOptimizer(objective="sharpe_ratio")
        result = opt.grid_search(
            tick_data=[],
            param_grid={
                "min_spread_pct": [0.1, 0.2, 0.3],
            },
            pipeline_factory=factory,
            engine_factory=MockEngine,
        )

        assert isinstance(result, OptimizationResult)
        assert result.best_score > 0
        assert len(result.all_results) == 3
        assert result.optimization_time_seconds >= 0
        # Best should be min_spread_pct=0.3 (sharpe=3.0)
        assert result.best_params["min_spread_pct"] == 0.3

    def test_grid_search_multi_param(self) -> None:
        """Grid search with multiple parameters."""

        def factory(params: dict) -> MockPipeline:
            pnl = params.get("a", 0) + params.get("b", 0)
            return MockPipeline(_make_backtest_result(total_pnl=pnl))

        opt = ParamOptimizer(objective="total_pnl")
        result = opt.grid_search(
            tick_data=[],
            param_grid={"a": [10.0, 20.0], "b": [100.0, 200.0]},
            pipeline_factory=factory,
            engine_factory=MockEngine,
        )

        assert result.best_params == {"a": 20.0, "b": 200.0}
        assert result.best_score == 220.0
        assert len(result.all_results) == 4

    def test_grid_search_results_sorted_descending(self) -> None:
        """Results should be sorted by score descending."""

        def factory(params: dict) -> MockPipeline:
            return MockPipeline(
                _make_backtest_result(sharpe_ratio=params["x"])
            )

        opt = ParamOptimizer(objective="sharpe_ratio")
        result = opt.grid_search(
            tick_data=[],
            param_grid={"x": [1.0, 3.0, 2.0, 5.0, 4.0]},
            pipeline_factory=factory,
            engine_factory=MockEngine,
        )

        scores = [r.score for r in result.all_results]
        assert scores == sorted(scores, reverse=True)

    def test_grid_search_drawdown_penalty(self) -> None:
        """Results exceeding drawdown constraint should be penalized."""

        def factory(params: dict) -> MockPipeline:
            return MockPipeline(
                _make_backtest_result(
                    sharpe_ratio=3.0,
                    max_drawdown_pct=params["dd"],
                )
            )

        opt = ParamOptimizer(objective="sharpe_ratio", max_drawdown_constraint=5.0)
        result = opt.grid_search(
            tick_data=[],
            param_grid={"dd": [3.0, 8.0]},
            pipeline_factory=factory,
            engine_factory=MockEngine,
        )

        # dd=3.0 has no penalty (score=3.0)
        # dd=8.0 has penalty: 3.0 - (8.0-5.0)*0.5 = 1.5
        assert result.best_params["dd"] == 3.0
        assert result.best_score == 3.0
        penalized = [r for r in result.all_results if r.params["dd"] == 8.0][0]
        assert penalized.score == 1.5

    def test_bayesian_optimize_basic(self) -> None:
        """Bayesian optimization should run and return a result."""
        eval_count = 0

        def factory(params: dict) -> MockPipeline:
            nonlocal eval_count
            eval_count += 1
            # Simple concave function with max at x=5
            x = params.get("x", 0)
            sharpe = -((x - 5.0) ** 2) + 25
            return MockPipeline(_make_backtest_result(sharpe_ratio=sharpe))

        opt = ParamOptimizer(objective="sharpe_ratio")
        result = opt.bayesian_optimize(
            tick_data=[],
            param_bounds={"x": (0.0, 10.0)},
            n_iter=20,
            pipeline_factory=factory,
            engine_factory=MockEngine,
        )

        assert isinstance(result, OptimizationResult)
        assert result.best_score > 0
        assert len(result.all_results) > 0
        assert result.optimization_time_seconds >= 0

    def test_param_score_model(self) -> None:
        """ParamScore model should store all fields."""
        ps = ParamScore(
            params={"x": 1.0},
            score=2.5,
            total_pnl=100.0,
            sharpe_ratio=2.5,
            win_rate=0.6,
            max_drawdown_pct=3.0,
            total_trades=50,
        )
        assert ps.params == {"x": 1.0}
        assert ps.score == 2.5
        assert ps.total_trades == 50


# ── StrategyComparator Tests ──────────────────────────────────────


class TestStrategyComparator:
    """Tests for StrategyComparator."""

    def test_compare_single_strategy(self) -> None:
        """Comparison with a single strategy."""

        def factory_a(params: dict) -> MockPipeline:
            return MockPipeline(_make_backtest_result(total_pnl=500.0, sharpe_ratio=2.0))

        comparator = StrategyComparator()
        strategies = [StrategyConfig(name="spatial", params={"min_spread": 0.25})]

        report = comparator.compare(
            strategies=strategies,
            tick_data=[],
            pipeline_factories={"spatial": factory_a},
            engine_factory=MockEngine,
        )

        assert isinstance(report, ComparisonReport)
        assert len(report.results) == 1
        assert report.results[0].strategy_name == "spatial"
        assert report.results[0].total_pnl == 500.0
        assert report.best_overall == "spatial"

    def test_compare_multiple_strategies(self) -> None:
        """Comparison across multiple strategies with rankings."""

        def factory_a(params: dict) -> MockPipeline:
            return MockPipeline(_make_backtest_result(
                total_pnl=500.0, sharpe_ratio=2.0, win_rate=0.7,
                max_drawdown_pct=3.0, profit_factor=2.0, total_trades=100,
            ))

        def factory_b(params: dict) -> MockPipeline:
            return MockPipeline(_make_backtest_result(
                total_pnl=800.0, sharpe_ratio=1.5, win_rate=0.5,
                max_drawdown_pct=5.0, profit_factor=1.2, total_trades=200,
            ))

        def factory_c(params: dict) -> MockPipeline:
            return MockPipeline(_make_backtest_result(
                total_pnl=300.0, sharpe_ratio=3.0, win_rate=0.8,
                max_drawdown_pct=1.0, profit_factor=3.0, total_trades=50,
            ))

        comparator = StrategyComparator()
        strategies = [
            StrategyConfig(name="spatial"),
            StrategyConfig(name="triangular"),
            StrategyConfig(name="statistical"),
        ]

        report = comparator.compare(
            strategies=strategies,
            tick_data=[],
            pipeline_factories={
                "spatial": factory_a,
                "triangular": factory_b,
                "statistical": factory_c,
            },
            engine_factory=MockEngine,
        )

        assert len(report.results) == 3
        assert "total_pnl" in report.rankings
        assert "sharpe_ratio" in report.rankings
        assert "max_drawdown_pct" in report.rankings

        # Check total_pnl ranking: triangular (800) > spatial (500) > statistical (300)
        assert report.rankings["total_pnl"] == [
            "triangular", "spatial", "statistical"
        ]

        # Check sharpe ranking: statistical (3.0) > spatial (2.0) > triangular (1.5)
        assert report.rankings["sharpe_ratio"] == [
            "statistical", "spatial", "triangular"
        ]

        # max_drawdown: lower is better
        # statistical (1.0) > spatial (3.0) > triangular (5.0)
        assert report.rankings["max_drawdown_pct"] == [
            "statistical", "spatial", "triangular"
        ]

        # best_overall should be non-empty
        assert report.best_overall != ""

    def test_compare_missing_factory_skipped(self) -> None:
        """Strategies with missing factory are skipped."""
        comparator = StrategyComparator()
        strategies = [
            StrategyConfig(name="spatial"),
            StrategyConfig(name="missing"),
        ]

        def factory_a(params: dict) -> MockPipeline:
            return MockPipeline()

        report = comparator.compare(
            strategies=strategies,
            tick_data=[],
            pipeline_factories={"spatial": factory_a},
            engine_factory=MockEngine,
        )

        assert len(report.results) == 1
        assert report.results[0].strategy_name == "spatial"

    def test_compare_empty_strategies(self) -> None:
        """Comparison with no strategies."""
        comparator = StrategyComparator()
        report = comparator.compare(
            strategies=[],
            tick_data=[],
            pipeline_factories={},
            engine_factory=MockEngine,
        )
        assert len(report.results) == 0
        assert report.best_overall == ""

    def test_strategy_result_fields(self) -> None:
        """StrategyResult should have all expected fields."""
        sr = StrategyResult(
            strategy_name="test",
            total_pnl=100.0,
            sharpe_ratio=2.0,
            max_drawdown_pct=3.0,
            win_rate=0.6,
            total_trades=50,
            profit_factor=1.5,
            avg_profit_per_trade=2.0,
        )
        assert sr.strategy_name == "test"
        assert sr.total_pnl == 100.0


# ── DivergenceAnalyzer Tests ──────────────────────────────────────


class TestDivergenceAnalyzer:
    """Tests for DivergenceAnalyzer."""

    def test_empty_trades(self) -> None:
        """Empty input should produce zero-value report."""
        analyzer = DivergenceAnalyzer()
        report = analyzer.analyze([], [])

        assert report.pnl_correlation == 0.0
        assert report.mean_divergence_pct == 0.0
        assert report.signal_match_rate == 0.0
        assert report.systematic_bias == 0.0
        assert report.paper_trade_count == 0
        assert report.backtest_trade_count == 0

    def test_perfectly_matched_trades(self) -> None:
        """Identical trades should have high correlation."""
        trades = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=-5.0),
            TradeRecord(timestamp=300.0, symbol="BTC/USDT", pnl=15.0),
            TradeRecord(timestamp=400.0, symbol="BTC/USDT", pnl=-8.0),
        ]

        analyzer = DivergenceAnalyzer(timestamp_tolerance_seconds=5.0)
        report = analyzer.analyze(trades, trades)

        assert report.pnl_correlation == pytest.approx(1.0)
        assert report.mean_divergence_pct == 0.0
        assert report.signal_match_rate == 1.0
        assert report.systematic_bias == 0.0
        assert report.paper_total_pnl == 12.0
        assert report.backtest_total_pnl == 12.0

    def test_partial_match(self) -> None:
        """Some trades match, some don't."""
        paper = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=-5.0),
            TradeRecord(timestamp=500.0, symbol="ETH/USDT", pnl=3.0),
        ]
        backtest = [
            TradeRecord(timestamp=101.0, symbol="BTC/USDT", pnl=12.0),
            TradeRecord(timestamp=201.0, symbol="BTC/USDT", pnl=-3.0),
            # No match for the ETH trade at 500.0
        ]

        analyzer = DivergenceAnalyzer(timestamp_tolerance_seconds=5.0)
        report = analyzer.analyze(paper, backtest)

        # 2 out of 3 paper trades matched
        assert report.signal_match_rate == pytest.approx(2.0 / 3.0)
        assert report.paper_trade_count == 3
        assert report.backtest_trade_count == 2

    def test_systematic_bias_positive(self) -> None:
        """Paper consistently outperforms backtest."""
        paper = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=15.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=20.0),
        ]
        backtest = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=12.0),
        ]

        analyzer = DivergenceAnalyzer()
        report = analyzer.analyze(paper, backtest)

        # Average bias = ((15-10) + (20-12)) / 2 = 6.5
        assert report.systematic_bias > 0
        assert report.systematic_bias == pytest.approx(6.5)

    def test_systematic_bias_negative(self) -> None:
        """Paper consistently underperforms backtest."""
        paper = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=5.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=8.0),
        ]
        backtest = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=15.0),
        ]

        analyzer = DivergenceAnalyzer()
        report = analyzer.analyze(paper, backtest)

        assert report.systematic_bias < 0

    def test_recommendations_low_correlation(self) -> None:
        """Low correlation should produce recommendation."""
        paper = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=-20.0),
            TradeRecord(timestamp=300.0, symbol="BTC/USDT", pnl=5.0),
        ]
        backtest = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=-15.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=25.0),
            TradeRecord(timestamp=300.0, symbol="BTC/USDT", pnl=-10.0),
        ]

        analyzer = DivergenceAnalyzer()
        report = analyzer.analyze(paper, backtest)

        assert len(report.recommendations) > 0
        # Should mention correlation
        has_correlation_rec = any(
            "correlation" in r.lower() for r in report.recommendations
        )
        assert has_correlation_rec

    def test_recommendations_good_alignment(self) -> None:
        """Well-aligned results should get a positive recommendation."""
        trades = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=-5.0),
            TradeRecord(timestamp=300.0, symbol="BTC/USDT", pnl=15.0),
            TradeRecord(timestamp=400.0, symbol="BTC/USDT", pnl=-3.0),
        ]

        analyzer = DivergenceAnalyzer()
        report = analyzer.analyze(trades, trades)

        assert any("well aligned" in r.lower() for r in report.recommendations)

    def test_timestamp_tolerance(self) -> None:
        """Trades outside tolerance should not match."""
        paper = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
        ]
        backtest = [
            TradeRecord(timestamp=200.0, symbol="BTC/USDT", pnl=10.0),
        ]

        # Tight tolerance - should not match
        analyzer = DivergenceAnalyzer(timestamp_tolerance_seconds=1.0)
        report = analyzer.analyze(paper, backtest)
        assert report.signal_match_rate == 0.0

        # Wide tolerance - should match
        analyzer = DivergenceAnalyzer(timestamp_tolerance_seconds=200.0)
        report = analyzer.analyze(paper, backtest)
        assert report.signal_match_rate == 1.0

    def test_symbol_must_match(self) -> None:
        """Trades on different symbols should not match."""
        paper = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
        ]
        backtest = [
            TradeRecord(timestamp=100.0, symbol="ETH/USDT", pnl=10.0),
        ]

        analyzer = DivergenceAnalyzer()
        report = analyzer.analyze(paper, backtest)
        assert report.signal_match_rate == 0.0

    def test_divergence_report_fields(self) -> None:
        """DivergenceReport should have all expected fields."""
        report = DivergenceReport(
            pnl_correlation=0.9,
            mean_divergence_pct=5.0,
            signal_match_rate=0.8,
            systematic_bias=-0.5,
            paper_total_pnl=100.0,
            backtest_total_pnl=120.0,
            paper_trade_count=50,
            backtest_trade_count=60,
            recommendations=["Check slippage"],
        )
        assert report.pnl_correlation == 0.9
        assert report.paper_trade_count == 50
        assert len(report.recommendations) == 1

    def test_recommendations_low_signal_match(self) -> None:
        """Low signal match rate should produce a recommendation."""
        paper = [
            TradeRecord(timestamp=100.0, symbol="BTC/USDT", pnl=10.0),
            TradeRecord(timestamp=200.0, symbol="ETH/USDT", pnl=-5.0),
            TradeRecord(timestamp=300.0, symbol="SOL/USDT", pnl=3.0),
        ]
        # No matching backtest trades
        backtest: list[TradeRecord] = []

        analyzer = DivergenceAnalyzer()
        report = analyzer.analyze(paper, backtest)

        assert report.signal_match_rate == 0.0
        has_match_rec = any(
            "match rate" in r.lower() for r in report.recommendations
        )
        assert has_match_rec
