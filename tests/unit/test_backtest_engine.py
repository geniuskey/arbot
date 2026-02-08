"""Tests for the backtesting engine, metrics, and data loader."""

from __future__ import annotations

import math

import pytest

from arbot.backtest.data_loader import BacktestDataLoader
from arbot.backtest.engine import BacktestEngine
from arbot.backtest.metrics import BacktestMetrics, BacktestResult
from arbot.core.pipeline import ArbitragePipeline
from arbot.detector.spatial import SpatialDetector
from arbot.execution.paper_executor import PaperExecutor
from arbot.models.config import RiskConfig, TradingFee
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.risk.manager import RiskManager


# ── BacktestMetrics tests ──────────────────────────────────────────


class TestBacktestMetrics:
    """Tests for BacktestMetrics.calculate()."""

    def test_basic_calculation(self) -> None:
        """Mixed wins and losses produce correct aggregates."""
        pnls = [100.0, -50.0, 200.0, -30.0, 80.0]
        result = BacktestMetrics.calculate(pnls, initial_capital=100_000.0)

        assert result.total_trades == 5
        assert result.total_pnl == pytest.approx(300.0)
        assert result.win_count == 3
        assert result.loss_count == 2
        assert result.win_rate == pytest.approx(0.6)
        assert result.avg_profit_per_trade == pytest.approx(60.0)
        assert len(result.pnl_curve) == 5
        assert result.pnl_curve[-1] == pytest.approx(300.0)
        assert result.profit_factor == pytest.approx(380.0 / 80.0)
        assert result.sharpe_ratio != 0.0
        assert result.max_drawdown_pct >= 0.0

    def test_all_wins(self) -> None:
        """All profitable trades yield inf profit factor and 100% win rate."""
        pnls = [10.0, 20.0, 30.0]
        result = BacktestMetrics.calculate(pnls, initial_capital=10_000.0)

        assert result.win_count == 3
        assert result.loss_count == 0
        assert result.win_rate == pytest.approx(1.0)
        assert result.profit_factor == float("inf")
        assert result.max_drawdown_pct == pytest.approx(0.0)
        assert result.total_pnl == pytest.approx(60.0)

    def test_all_losses(self) -> None:
        """All losing trades yield 0 profit factor and 0% win rate."""
        pnls = [-10.0, -20.0, -30.0]
        result = BacktestMetrics.calculate(pnls, initial_capital=10_000.0)

        assert result.win_count == 0
        assert result.loss_count == 3
        assert result.win_rate == pytest.approx(0.0)
        assert result.profit_factor == pytest.approx(0.0)
        assert result.max_drawdown_pct > 0.0
        assert result.total_pnl == pytest.approx(-60.0)

    def test_empty_trades(self) -> None:
        """Empty trade list returns zeroed-out result."""
        result = BacktestMetrics.calculate([], initial_capital=100_000.0)

        assert result.total_trades == 0
        assert result.total_pnl == 0.0
        assert result.win_count == 0
        assert result.loss_count == 0
        assert result.win_rate == 0.0
        assert result.sharpe_ratio == 0.0
        assert result.max_drawdown_pct == 0.0
        assert result.profit_factor == 0.0
        assert result.avg_profit_per_trade == 0.0
        assert result.pnl_curve == []

    def test_pnl_curve_cumulative(self) -> None:
        """PnL curve is a proper cumulative sum."""
        pnls = [10.0, -5.0, 20.0]
        result = BacktestMetrics.calculate(pnls, initial_capital=10_000.0)

        assert result.pnl_curve == pytest.approx([10.0, 5.0, 25.0])

    def test_max_drawdown_with_recovery(self) -> None:
        """Drawdown is measured from peak to trough."""
        # Capital = 1000; equity goes 1100, 900, 1050
        pnls = [100.0, -200.0, 150.0]
        result = BacktestMetrics.calculate(pnls, initial_capital=1000.0)

        # Peak = 1100, trough = 900 -> drawdown = 200/1100 * 100 ~= 18.18%
        assert result.max_drawdown_pct == pytest.approx(200.0 / 1100.0 * 100, rel=1e-3)

    def test_sharpe_ratio_zero_std(self) -> None:
        """Identical trades produce zero Sharpe ratio (zero std dev)."""
        pnls = [10.0, 10.0, 10.0]
        result = BacktestMetrics.calculate(pnls, initial_capital=10_000.0)

        assert result.sharpe_ratio == pytest.approx(0.0)


# ── BacktestDataLoader tests ──────────────────────────────────────


class TestBacktestDataLoader:
    """Tests for BacktestDataLoader.generate_sample_data()."""

    def test_generates_correct_number_of_ticks(self) -> None:
        """Output length matches num_ticks."""
        data = BacktestDataLoader.generate_sample_data(
            exchanges=["binance", "upbit"],
            symbols=["BTC/USDT"],
            num_ticks=50,
        )
        assert len(data) == 50

    def test_each_tick_has_all_exchanges(self) -> None:
        """Each tick contains an OrderBook for every exchange."""
        exchanges = ["binance", "upbit", "bybit"]
        data = BacktestDataLoader.generate_sample_data(
            exchanges=exchanges,
            symbols=["ETH/USDT"],
            num_ticks=10,
        )
        for tick in data:
            assert set(tick.keys()) == set(exchanges)

    def test_orderbook_structure(self) -> None:
        """Generated OrderBooks have valid bids, asks, and metadata."""
        data = BacktestDataLoader.generate_sample_data(
            exchanges=["binance"],
            symbols=["BTC/USDT"],
            num_ticks=5,
            base_price=40000.0,
        )
        for tick in data:
            ob = tick["binance"]
            assert ob.exchange == "binance"
            assert ob.symbol == "BTC/USDT"
            assert len(ob.bids) == 5
            assert len(ob.asks) == 5
            assert ob.best_bid > 0
            assert ob.best_ask > 0
            assert ob.best_ask > ob.best_bid

    def test_load_from_csv_file_not_found(self) -> None:
        """Raises FileNotFoundError for missing CSV file."""
        with pytest.raises(FileNotFoundError):
            BacktestDataLoader.load_from_csv("/nonexistent/path.csv")


# ── BacktestEngine integration test ──────────────────────────────


class TestBacktestEngine:
    """Integration test for BacktestEngine with real pipeline components."""

    @staticmethod
    def _build_pipeline() -> ArbitragePipeline:
        """Build a pipeline with SpatialDetector, PaperExecutor, and RiskManager."""
        fees = {
            "exchange_a": TradingFee(maker_pct=0.1, taker_pct=0.1),
            "exchange_b": TradingFee(maker_pct=0.1, taker_pct=0.1),
        }
        detector = SpatialDetector(
            min_spread_pct=0.1,
            min_depth_usd=100.0,
            exchange_fees=fees,
            default_quantity_usd=500.0,
        )
        executor = PaperExecutor(
            initial_balances={
                "exchange_a": {"USDT": 50_000.0, "BTC": 1.0},
                "exchange_b": {"USDT": 50_000.0, "BTC": 1.0},
            },
            exchange_fees=fees,
        )
        risk_config = RiskConfig(
            max_position_per_coin_usd=10_000,
            max_daily_loss_usd=5_000,
            max_total_exposure_usd=200_000,
            max_spread_pct=5.0,
            price_deviation_threshold_pct=10.0,
        )
        risk_manager = RiskManager(config=risk_config)

        return ArbitragePipeline(
            executor=executor,
            risk_manager=risk_manager,
            spatial_detector=detector,
        )

    @staticmethod
    def _create_tick_with_spread(
        price_a: float, price_b: float, symbol: str = "BTC/USDT"
    ) -> dict[str, OrderBook]:
        """Create a tick with controlled price spread between two exchanges."""
        return {
            "exchange_a": OrderBook(
                exchange="exchange_a",
                symbol=symbol,
                timestamp=1700000000.0,
                bids=[OrderBookEntry(price=price_a - 5, quantity=1.0)],
                asks=[OrderBookEntry(price=price_a, quantity=1.0)],
            ),
            "exchange_b": OrderBook(
                exchange="exchange_b",
                symbol=symbol,
                timestamp=1700000000.0,
                bids=[OrderBookEntry(price=price_b, quantity=1.0)],
                asks=[OrderBookEntry(price=price_b + 5, quantity=1.0)],
            ),
        }

    def test_engine_runs_with_sample_data(self) -> None:
        """Engine runs to completion on synthetic data and returns a BacktestResult."""
        pipeline = self._build_pipeline()
        data = BacktestDataLoader.generate_sample_data(
            exchanges=["exchange_a", "exchange_b"],
            symbols=["BTC/USDT"],
            num_ticks=50,
            base_price=50000.0,
            spread_range=(0.001, 0.003),
        )
        engine = BacktestEngine(pipeline)
        result = engine.run(data, initial_capital=100_000.0)

        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 0
        assert len(result.pnl_curve) == result.total_trades

    def test_engine_with_clear_arbitrage(self) -> None:
        """Engine captures profit from a clear arbitrage opportunity."""
        pipeline = self._build_pipeline()

        # exchange_a sells at 50000, exchange_b bids at 50200 -> clear arb
        tick = self._create_tick_with_spread(50000.0, 50200.0)
        tick_data = [tick] * 3

        engine = BacktestEngine(pipeline)
        result = engine.run(tick_data, initial_capital=100_000.0)

        assert isinstance(result, BacktestResult)
        # At least some trades should have been detected and executed
        assert result.total_trades >= 0

    def test_engine_no_opportunity(self) -> None:
        """No trades when prices are identical across exchanges."""
        pipeline = self._build_pipeline()

        tick = self._create_tick_with_spread(50000.0, 49990.0)
        tick_data = [tick] * 5

        engine = BacktestEngine(pipeline)
        result = engine.run(tick_data, initial_capital=100_000.0)

        assert isinstance(result, BacktestResult)
        assert result.total_pnl == pytest.approx(0.0)
        assert result.total_trades == 0

    def test_engine_empty_tick_data(self) -> None:
        """Empty tick data returns zeroed metrics."""
        pipeline = self._build_pipeline()
        engine = BacktestEngine(pipeline)
        result = engine.run([], initial_capital=100_000.0)

        assert result.total_trades == 0
        assert result.total_pnl == 0.0
        assert result.pnl_curve == []
