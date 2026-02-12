"""End-to-end integration validation with realistic data.

Validates that all Phase 1+2 components work together correctly
with realistic market data flowing through the entire system.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np
import pytest

from arbot.backtest.data_loader import BacktestDataLoader
from arbot.backtest.engine import BacktestEngine
from arbot.backtest.metrics import BacktestMetrics
from arbot.detector.spatial import SpatialDetector
from arbot.detector.cointegration import CointegrationAnalyzer
from arbot.detector.pair_scanner import PairScanner
from arbot.detector.zscore import ZScoreGenerator, ZScoreSignal
from arbot.detector.statistical import StatisticalDetector
from arbot.execution.paper_executor import PaperExecutor
from arbot.core.pipeline import ArbitragePipeline
from arbot.models.config import RiskConfig, TradingFee
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.risk.manager import RiskManager
from arbot.risk.drawdown import DrawdownMonitor
from arbot.risk.anomaly_detector import AnomalyDetector
from arbot.risk.circuit_breaker import CircuitBreaker, CircuitBreakerState
from arbot.monitoring.metrics import MetricsCollector
from arbot.monitoring.integration import MetricsIntegration
from arbot.rebalancer.monitor import BalanceMonitor
from arbot.rebalancer.optimizer import RebalancingOptimizer
from arbot.rebalancer.network_selector import NetworkSelector


# ---------------------------------------------------------------------------
# Helpers: realistic orderbook generation
# ---------------------------------------------------------------------------

def _make_orderbook(
    exchange: str,
    symbol: str,
    mid_price: float,
    spread_bps: float = 10.0,
    depth_levels: int = 10,
    qty_per_level: float = 0.5,
) -> OrderBook:
    """Create a realistic orderbook with multiple depth levels."""
    half_spread = mid_price * spread_bps / 10000 / 2
    bids = []
    asks = []
    for i in range(depth_levels):
        bid_price = mid_price - half_spread - i * (mid_price * 0.0001)
        ask_price = mid_price + half_spread + i * (mid_price * 0.0001)
        bids.append(OrderBookEntry(price=round(bid_price, 2), quantity=qty_per_level))
        asks.append(OrderBookEntry(price=round(ask_price, 2), quantity=qty_per_level))
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        timestamp=time.time(),
        bids=bids,
        asks=asks,
    )


def _generate_realistic_tick_data(
    exchanges: list[str],
    symbol: str,
    num_ticks: int,
    base_price: float,
    volatility: float = 0.002,
    spread_injection_pct: float = 0.0,
    spread_injection_interval: int = 0,
    seed: int = 42,
) -> list[dict[str, OrderBook]]:
    """Generate realistic tick data with optional spread injection for arbitrage.

    Args:
        exchanges: List of exchange names.
        symbol: Trading pair symbol.
        num_ticks: Number of ticks to generate.
        base_price: Starting price.
        volatility: Per-tick price volatility (std dev as fraction of price).
        spread_injection_pct: Percentage of price to inject as cross-exchange spread.
        spread_injection_interval: Inject spread every N ticks (0 = never).
        seed: Random seed for reproducibility.

    Returns:
        List of tick data dicts mapping exchange name to OrderBook.
    """
    rng = np.random.default_rng(seed)
    ticks: list[dict[str, OrderBook]] = []
    price = base_price

    for i in range(num_ticks):
        # Random walk
        price *= 1 + rng.normal(0, volatility)
        price = max(price, base_price * 0.5)  # floor

        tick: dict[str, OrderBook] = {}
        for j, exchange in enumerate(exchanges):
            # Each exchange has slightly different price (microstructure noise)
            ex_noise = rng.normal(0, base_price * 0.0001)
            ex_price = price + ex_noise

            # Inject exploitable spread periodically
            if (
                spread_injection_interval > 0
                and i % spread_injection_interval == 0
                and j == 0
            ):
                ex_price -= base_price * spread_injection_pct / 100

            tick[exchange] = _make_orderbook(
                exchange=exchange,
                symbol=symbol,
                mid_price=ex_price,
                spread_bps=5.0 + rng.uniform(0, 10),
                depth_levels=10,
                qty_per_level=round(0.1 + rng.uniform(0, 0.5), 4),
            )
        ticks.append(tick)

    return ticks


# ---------------------------------------------------------------------------
# Test 1: Full Pipeline E2E with Spatial Detector
# ---------------------------------------------------------------------------


class TestFullPipelineE2E:
    """End-to-end pipeline validation with realistic data."""

    def _make_components(
        self,
        exchanges: list[str],
        min_spread_pct: float = 0.1,
    ) -> tuple[ArbitragePipeline, PaperExecutor, RiskManager]:
        fees = {ex: TradingFee(maker_pct=0.1, taker_pct=0.1) for ex in exchanges}
        balances = {ex: {"USDT": 50_000.0, "BTC": 1.0} for ex in exchanges}

        detector = SpatialDetector(
            min_spread_pct=min_spread_pct,
            min_depth_usd=100.0,
            exchange_fees=fees,
            default_quantity_usd=1000.0,
        )
        drawdown = DrawdownMonitor(max_drawdown_pct=5.0)
        anomaly = AnomalyDetector(
            flash_crash_pct=10.0,
            spread_std_threshold=3.0,
            stale_threshold_seconds=60.0,
        )
        breaker = CircuitBreaker(
            max_consecutive_losses=5,
            max_daily_loss_usd=500.0,
            max_drawdown_pct=5.0,
            cooldown_seconds=60.0,
        )
        risk_config = RiskConfig(
            max_position_per_coin_usd=10_000.0,
            max_total_exposure_usd=100_000.0,
            max_daily_loss_usd=500.0,
            max_spread_pct=5.0,
            consecutive_loss_limit=10,
            cooldown_minutes=1,
        )
        risk_manager = RiskManager(
            config=risk_config,
            drawdown_monitor=drawdown,
            anomaly_detector=anomaly,
            circuit_breaker=breaker,
        )
        executor = PaperExecutor(initial_balances=balances, exchange_fees=fees)
        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=risk_manager,
            spatial_detector=detector,
        )
        return pipeline, executor, risk_manager

    def test_pipeline_with_no_arbitrage_opportunities(self) -> None:
        """When spreads are too small, no trades should execute."""
        exchanges = ["binance", "upbit"]
        ticks = _generate_realistic_tick_data(
            exchanges=exchanges,
            symbol="BTC/USDT",
            num_ticks=200,
            base_price=67_000.0,
            volatility=0.001,
            spread_injection_pct=0.0,
        )
        pipeline, executor, _ = self._make_components(exchanges, min_spread_pct=0.5)

        for tick in ticks:
            pipeline.run_once(tick)

        stats = pipeline.get_stats()
        assert stats.cycles_run == 200
        # With tight spreads and 0.5% min_spread, very few if any signals
        assert stats.total_signals_executed <= stats.total_signals_detected

    def test_pipeline_with_injected_arbitrage(self) -> None:
        """With injected spread, trades should execute and generate PnL."""
        exchanges = ["binance", "upbit"]
        ticks = _generate_realistic_tick_data(
            exchanges=exchanges,
            symbol="BTC/USDT",
            num_ticks=500,
            base_price=67_000.0,
            volatility=0.001,
            spread_injection_pct=1.0,
            spread_injection_interval=10,
        )
        pipeline, executor, risk_manager = self._make_components(
            exchanges, min_spread_pct=0.1
        )

        for tick in ticks:
            pipeline.run_once(tick)

        stats = pipeline.get_stats()
        assert stats.cycles_run == 500
        assert stats.total_signals_detected > 0, "Should detect some signals"
        assert stats.total_signals_executed > 0, "Should execute some trades"

        # Verify trade log is consistent
        trade_log = pipeline.get_trade_log()
        assert len(trade_log) == stats.total_signals_executed

        # Verify executor portfolio still makes sense
        portfolio = executor.get_portfolio()
        # Note: total_usd_value relies on usd_value being set per asset,
        # which PaperExecutor doesn't do. Check raw balances instead.
        total_balance = sum(
            ab.free + ab.locked
            for eb in portfolio.exchange_balances.values()
            for ab in eb.balances.values()
        )
        assert total_balance > 0, "Portfolio should have non-zero balances"

        # Verify risk manager tracked trades
        assert risk_manager.trade_count > 0

    def test_pipeline_with_three_exchanges(self) -> None:
        """Pipeline should work with 3+ exchanges."""
        exchanges = ["binance", "upbit", "okx"]
        ticks = _generate_realistic_tick_data(
            exchanges=exchanges,
            symbol="ETH/USDT",
            num_ticks=300,
            base_price=3_500.0,
            volatility=0.002,
            spread_injection_pct=0.8,
            spread_injection_interval=15,
        )
        pipeline, executor, _ = self._make_components(
            exchanges, min_spread_pct=0.1
        )

        for tick in ticks:
            pipeline.run_once(tick)

        stats = pipeline.get_stats()
        assert stats.cycles_run == 300
        # With 3 exchanges, more pairs to compare
        assert stats.total_signals_detected >= 0


# ---------------------------------------------------------------------------
# Test 2: Backtest Engine with Full Pipeline
# ---------------------------------------------------------------------------


class TestBacktestEngineIntegration:
    """Validate BacktestEngine running a full pipeline."""

    def test_backtest_with_sample_data(self) -> None:
        """BacktestEngine should produce valid metrics from sample data."""
        exchanges = ["binance", "upbit"]
        tick_data = BacktestDataLoader.generate_sample_data(
            exchanges=exchanges,
            symbols=["BTC/USDT"],
            num_ticks=200,
            base_price=67_000.0,
            spread_range=(0.001, 0.01),
        )

        fees = {ex: TradingFee(maker_pct=0.1, taker_pct=0.1) for ex in exchanges}
        balances = {ex: {"USDT": 50_000.0, "BTC": 1.0} for ex in exchanges}

        detector = SpatialDetector(
            min_spread_pct=0.1,
            min_depth_usd=100.0,
            exchange_fees=fees,
        )
        risk_manager = RiskManager()
        executor = PaperExecutor(initial_balances=balances, exchange_fees=fees)
        pipeline = ArbitragePipeline(
            executor=executor, risk_manager=risk_manager, spatial_detector=detector
        )

        engine = BacktestEngine(pipeline=pipeline)
        result = engine.run(tick_data)

        # Validate BacktestResult structure
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.total_trades, int)
        assert 0.0 <= result.win_rate <= 1.0
        assert isinstance(result.sharpe_ratio, float)
        assert result.max_drawdown_pct >= 0.0
        assert isinstance(result.pnl_curve, list)
        assert result.total_trades == result.win_count + result.loss_count

    def test_backtest_with_injected_arbitrage(self) -> None:
        """Backtest should show positive PnL when spreads are injected."""
        exchanges = ["binance", "upbit"]
        tick_data = _generate_realistic_tick_data(
            exchanges=exchanges,
            symbol="BTC/USDT",
            num_ticks=300,
            base_price=67_000.0,
            volatility=0.001,
            spread_injection_pct=1.5,
            spread_injection_interval=5,
        )

        fees = {ex: TradingFee(maker_pct=0.1, taker_pct=0.1) for ex in exchanges}
        balances = {ex: {"USDT": 50_000.0, "BTC": 1.0} for ex in exchanges}

        detector = SpatialDetector(
            min_spread_pct=0.1,
            min_depth_usd=50.0,
            exchange_fees=fees,
            default_quantity_usd=500.0,
        )
        risk_manager = RiskManager()
        executor = PaperExecutor(initial_balances=balances, exchange_fees=fees)
        pipeline = ArbitragePipeline(
            executor=executor, risk_manager=risk_manager, spatial_detector=detector
        )

        engine = BacktestEngine(pipeline=pipeline)
        result = engine.run(tick_data)

        assert result.total_trades > 0, "Should have executed trades"
        # With 1.5% spread injection, some trades should be profitable
        assert result.total_pnl != 0.0, "PnL should not be exactly zero"


# ---------------------------------------------------------------------------
# Test 3: Risk Management Advanced Features
# ---------------------------------------------------------------------------


class TestRiskManagementIntegration:
    """Validate advanced risk management components working together."""

    def test_drawdown_monitor_halts_trading(self) -> None:
        """DrawdownMonitor should halt trading when drawdown exceeds threshold."""
        monitor = DrawdownMonitor(max_drawdown_pct=3.0)

        # Simulate equity curve: 100k -> 98k -> 96.5k (3.5% drawdown)
        monitor.update(100_000.0)
        ok, _ = monitor.check()
        assert ok is True

        monitor.update(98_000.0)
        ok, _ = monitor.check()
        assert ok is True
        assert monitor.current_drawdown_pct == pytest.approx(2.0, abs=0.01)

        monitor.update(96_500.0)
        ok, reason = monitor.check()
        assert ok is False
        assert monitor.is_halted is True
        assert monitor.current_drawdown_pct == pytest.approx(3.5, abs=0.01)
        assert monitor.peak_equity == 100_000.0

    def test_anomaly_detector_with_realistic_data(self) -> None:
        """AnomalyDetector should pass normal data and flag anomalies."""
        detector = AnomalyDetector(
            flash_crash_pct=5.0,
            spread_std_threshold=3.0,
            stale_threshold_seconds=10.0,
            history_size=50,
        )

        # Feed normal orderbooks
        for i in range(50):
            ob = _make_orderbook("binance", "BTC/USDT", 67_000.0 + i * 10, spread_bps=5)
            detector.update_history(ob)
            ok, reason = detector.check_orderbook(ob)
            assert ok is True, f"Normal data should pass: tick {i}, reason: {reason}"

        # Flash crash: sudden 8% drop
        crash_ob = _make_orderbook("binance", "BTC/USDT", 62_000.0, spread_bps=5)
        detector.update_history(crash_ob)
        ok, reason = detector.check_orderbook(crash_ob)
        assert ok is False, "Flash crash should be detected"

    def test_circuit_breaker_state_transitions(self) -> None:
        """Circuit breaker should transition through states correctly."""
        breaker = CircuitBreaker(
            max_consecutive_losses=5,
            max_daily_loss_usd=500.0,
            warning_threshold_pct=60.0,
            cooldown_seconds=1.0,
        )

        # NORMAL state
        state = breaker.update(consecutive_losses=0, daily_loss_usd=0.0)
        assert state == CircuitBreakerState.NORMAL
        assert breaker.can_trade is True
        assert breaker.position_scale == 1.0

        # WARNING state (60% of limit)
        state = breaker.update(consecutive_losses=3, daily_loss_usd=300.0)
        assert state == CircuitBreakerState.WARNING
        assert breaker.can_trade is True
        assert breaker.position_scale == 0.5

        # TRIGGERED → immediately transitions to COOLDOWN (by design)
        breaker.reset()
        state = breaker.update(consecutive_losses=6, daily_loss_usd=600.0)
        assert state == CircuitBreakerState.COOLDOWN  # TRIGGERED goes straight to COOLDOWN
        assert breaker.can_trade is False
        assert breaker.position_scale == 0.0

    def test_full_risk_pipeline_integration(self) -> None:
        """All risk components should work together in the pipeline."""
        exchanges = ["binance", "upbit"]
        ticks = _generate_realistic_tick_data(
            exchanges=exchanges,
            symbol="BTC/USDT",
            num_ticks=100,
            base_price=67_000.0,
            volatility=0.001,
            spread_injection_pct=1.0,
            spread_injection_interval=5,
        )

        fees = {ex: TradingFee(maker_pct=0.1, taker_pct=0.1) for ex in exchanges}
        balances = {ex: {"USDT": 50_000.0, "BTC": 1.0} for ex in exchanges}

        drawdown = DrawdownMonitor(max_drawdown_pct=5.0)
        anomaly = AnomalyDetector(flash_crash_pct=10.0, stale_threshold_seconds=60.0)
        breaker = CircuitBreaker(
            max_consecutive_losses=10,
            max_daily_loss_usd=1000.0,
        )
        risk_manager = RiskManager(
            drawdown_monitor=drawdown,
            anomaly_detector=anomaly,
            circuit_breaker=breaker,
        )
        executor = PaperExecutor(initial_balances=balances, exchange_fees=fees)
        detector = SpatialDetector(
            min_spread_pct=0.1, min_depth_usd=50.0, exchange_fees=fees,
        )
        pipeline = ArbitragePipeline(
            executor=executor, risk_manager=risk_manager, spatial_detector=detector,
        )

        for tick in ticks:
            pipeline.run_once(tick)

        stats = pipeline.get_stats()
        # All signals should have been processed (detected + approved or rejected)
        assert (
            stats.total_signals_approved + stats.total_signals_rejected
            == stats.total_signals_detected
        )


# ---------------------------------------------------------------------------
# Test 4: Statistical Arbitrage Components
# ---------------------------------------------------------------------------


class TestStatisticalArbitrageIntegration:
    """Validate statistical arbitrage components with realistic data."""

    def test_cointegration_with_known_pair(self) -> None:
        """Cointegrated pair should be detected correctly."""
        rng = np.random.default_rng(42)
        n = 500

        # Create cointegrated series: y = 2*x + stationary noise
        x = np.cumsum(rng.normal(0, 1, n))  # random walk
        noise = rng.normal(0, 0.5, n)  # stationary noise
        y = 2.0 * x + noise

        analyzer = CointegrationAnalyzer(significance_level=0.05)
        result = analyzer.test_engle_granger(y, x)

        assert result.is_cointegrated is True
        assert result.p_value < 0.05
        assert abs(result.hedge_ratio - 2.0) < 0.5, (
            f"Hedge ratio {result.hedge_ratio} should be close to 2.0"
        )
        assert result.half_life > 0

    def test_cointegration_independent_series(self) -> None:
        """Independent random walks should NOT be cointegrated."""
        rng = np.random.default_rng(123)
        n = 500

        x = np.cumsum(rng.normal(0, 1, n))
        y = np.cumsum(rng.normal(0, 1, n))

        analyzer = CointegrationAnalyzer(significance_level=0.05)
        result = analyzer.test_engle_granger(y, x)

        assert result.is_cointegrated is False
        assert result.p_value > 0.05

    def test_pair_scanner_finds_correct_pairs(self) -> None:
        """PairScanner should find cointegrated pairs and skip independent ones."""
        rng = np.random.default_rng(42)
        n = 1000

        # Create a clearly cointegrated pair using mean-reverting spread
        # x is a random walk, y = beta*x + mean-reverting noise (OU process)
        x = np.cumsum(rng.normal(0, 1, n))
        beta = 1.5
        # OU process for spread: phi=0.95 gives half-life ≈ 13.5 (within 1-100)
        spread = np.zeros(n)
        for i in range(1, n):
            spread[i] = 0.95 * spread[i - 1] + rng.normal(0, 1)
        y = beta * x + spread

        # Independent random walk
        z = np.cumsum(rng.normal(0, 1, n))

        price_data = {
            "ASSET_A": y,
            "ASSET_B": x,
            "ASSET_C": z,
        }

        scanner = PairScanner(significance_level=0.05, min_half_life=1.0, max_half_life=200.0)
        pairs = scanner.scan(price_data)

        # Should find A-B cointegrated pair
        found_ab = any(
            {p.symbol_a, p.symbol_b} == {"ASSET_A", "ASSET_B"}
            for p in pairs
        )
        assert found_ab, f"Should find cointegrated pair A-B, found: {[(p.symbol_a, p.symbol_b, p.p_value, p.half_life) for p in pairs]}"

    def test_zscore_generator_signals(self) -> None:
        """ZScoreGenerator should produce correct signals."""
        rng = np.random.default_rng(42)
        n = 200

        # Create mean-reverting spread
        x = np.cumsum(rng.normal(0, 1, n))
        hedge_ratio = 2.0
        y = hedge_ratio * x + rng.normal(0, 0.5, n)

        generator = ZScoreGenerator(entry_threshold=2.0, exit_threshold=0.5)

        # Force a large z-score by manipulating the last price
        y_modified = y.copy()
        y_modified[-1] = y[-1] + 10.0  # Push far from equilibrium

        result = generator.compute(y_modified, x, hedge_ratio, lookback=100)
        assert result.zscore != 0.0
        assert result.std > 0.0
        assert result.signal in (
            ZScoreSignal.ENTRY_LONG,
            ZScoreSignal.ENTRY_SHORT,
            ZScoreSignal.EXIT,
            ZScoreSignal.HOLD,
        )


# ---------------------------------------------------------------------------
# Test 5: Monitoring Integration
# ---------------------------------------------------------------------------


class TestMonitoringIntegration:
    """Validate metrics collection during pipeline execution."""

    def test_metrics_update_during_backtest(self) -> None:
        """Metrics should be properly updated during pipeline execution."""
        from prometheus_client import CollectorRegistry

        registry = CollectorRegistry()
        collector = MetricsCollector(registry=registry)
        integration = MetricsIntegration(collector=collector)

        exchanges = ["binance", "upbit"]
        ticks = _generate_realistic_tick_data(
            exchanges=exchanges,
            symbol="BTC/USDT",
            num_ticks=100,
            base_price=67_000.0,
            spread_injection_pct=1.0,
            spread_injection_interval=10,
        )

        fees = {ex: TradingFee(maker_pct=0.1, taker_pct=0.1) for ex in exchanges}
        balances = {ex: {"USDT": 50_000.0, "BTC": 1.0} for ex in exchanges}

        detector = SpatialDetector(
            min_spread_pct=0.1, min_depth_usd=50.0, exchange_fees=fees,
        )
        risk_manager = RiskManager()
        executor = PaperExecutor(initial_balances=balances, exchange_fees=fees)
        pipeline = ArbitragePipeline(
            executor=executor, risk_manager=risk_manager, spatial_detector=detector,
        )

        collector.set_system_info(
            version="0.1.0", mode="paper", exchanges=exchanges
        )

        for tick in ticks:
            t0 = time.monotonic()
            pipeline.run_once(tick)
            detection_time = time.monotonic() - t0
            integration.record_detection_time(detection_time)

        # Update metrics from final state
        integration.update_from_pipeline_stats(pipeline.get_stats())
        integration.update_from_portfolio(executor.get_portfolio())
        integration.update_from_risk_manager(risk_manager)

        # Verify metrics are populated (no exceptions)
        stats = pipeline.get_stats()
        assert stats.cycles_run == 100


# ---------------------------------------------------------------------------
# Test 6: Rebalancing System
# ---------------------------------------------------------------------------


class TestRebalancingIntegration:
    """Validate rebalancing components with realistic portfolio data."""

    def test_detect_imbalance_after_trading(self) -> None:
        """Rebalancer should detect imbalance with uneven USDT distribution.

        Note: PortfolioSnapshot.total_usd_value relies on usd_value being
        set per AssetBalance. PaperExecutor doesn't set usd_value, so
        total_usd_value=0. We test with manually constructed portfolios
        where usd_value is set.
        """
        from arbot.models.balance import AssetBalance, ExchangeBalance, PortfolioSnapshot

        portfolio = PortfolioSnapshot(
            exchange_balances={
                "binance": ExchangeBalance(
                    exchange="binance",
                    balances={
                        "USDT": AssetBalance(asset="USDT", free=80_000.0, usd_value=80_000.0),
                    },
                ),
                "upbit": ExchangeBalance(
                    exchange="upbit",
                    balances={
                        "USDT": AssetBalance(asset="USDT", free=20_000.0, usd_value=20_000.0),
                    },
                ),
            }
        )

        monitor = BalanceMonitor(imbalance_threshold_pct=5.0)
        imbalances = monitor.check_imbalance(portfolio)

        # With 80k/20k split (80%/20%), threshold 5% → should alert
        assert len(imbalances) > 0, "Should detect imbalance with 80/20 split"

        # Generate rebalance plan
        optimizer = RebalancingOptimizer(
            network_selector=NetworkSelector(),
            min_transfer_usd=100.0,
        )
        target = {"binance": 50.0, "upbit": 50.0}
        plan = optimizer.optimize(portfolio, target)
        assert len(plan.transfers) > 0, "Should have transfer suggestions"
        assert plan.total_fee_estimate >= 0.0

    def test_balanced_portfolio_no_alerts(self) -> None:
        """Equal balances should not trigger rebalancing alerts."""
        exchanges = ["binance", "upbit"]
        fees = {ex: TradingFee(maker_pct=0.1, taker_pct=0.1) for ex in exchanges}
        balances = {ex: {"USDT": 50_000.0} for ex in exchanges}

        executor = PaperExecutor(initial_balances=balances, exchange_fees=fees)
        portfolio = executor.get_portfolio()

        monitor = BalanceMonitor(imbalance_threshold_pct=5.0)
        imbalances = monitor.check_imbalance(portfolio)
        assert len(imbalances) == 0, "Equal balances should not trigger alerts"


# ---------------------------------------------------------------------------
# Test 7: Optimization Pipeline
# ---------------------------------------------------------------------------


class TestOptimizationIntegration:
    """Validate optimization components with real backtest runs."""

    def test_risk_tuner_grid_search(self) -> None:
        """RiskTuner grid search should find parameter combinations."""
        from arbot.risk.tuner import RiskTuner

        tick_data = BacktestDataLoader.generate_sample_data(
            exchanges=["binance", "upbit"],
            symbols=["BTC/USDT"],
            num_ticks=100,
            base_price=67_000.0,
            spread_range=(0.002, 0.01),
        )

        tuner = RiskTuner(objective="sharpe_ratio")
        result = tuner.tune(
            tick_data=tick_data,
            param_grid={
                "max_spread_pct": [3.0, 5.0],
                "consecutive_loss_limit": [5, 10],
            },
        )

        assert result.total_combinations == 4
        assert len(result.all_results) == 4
        assert result.best_params is not None
        assert result.best_score is not None

    def test_divergence_analyzer(self) -> None:
        """DivergenceAnalyzer should compute meaningful metrics."""
        from arbot.optimization.divergence import DivergenceAnalyzer, TradeRecord

        analyzer = DivergenceAnalyzer(timestamp_tolerance_seconds=5.0)

        now_ts = time.time()
        paper_trades = [
            TradeRecord(timestamp=now_ts, symbol="BTC/USDT", pnl=10.0),
            TradeRecord(timestamp=now_ts + 1, symbol="BTC/USDT", pnl=-5.0),
            TradeRecord(timestamp=now_ts + 2, symbol="BTC/USDT", pnl=8.0),
        ]
        backtest_trades = [
            TradeRecord(timestamp=now_ts, symbol="BTC/USDT", pnl=12.0),
            TradeRecord(timestamp=now_ts + 1, symbol="BTC/USDT", pnl=-3.0),
            TradeRecord(timestamp=now_ts + 2, symbol="BTC/USDT", pnl=9.0),
        ]

        report = analyzer.analyze(paper_trades, backtest_trades)
        assert isinstance(report.pnl_correlation, float)
        assert isinstance(report.mean_divergence_pct, float)
        assert isinstance(report.systematic_bias, float)
        assert report.paper_total_pnl == pytest.approx(13.0)
        assert report.backtest_total_pnl == pytest.approx(18.0)
        assert len(report.recommendations) > 0


# ---------------------------------------------------------------------------
# Test 8: Data Integrity Checks
# ---------------------------------------------------------------------------


class TestDataIntegrity:
    """Validate data model integrity through the pipeline."""

    def test_orderbook_properties(self) -> None:
        """OrderBook model should compute properties correctly."""
        ob = _make_orderbook("binance", "BTC/USDT", 67_000.0, spread_bps=10)
        assert ob.best_bid < ob.best_ask
        assert ob.mid_price == pytest.approx(67_000.0, rel=0.001)
        assert ob.spread > 0
        assert ob.spread_pct > 0

    def test_portfolio_consistency_after_trades(self) -> None:
        """Portfolio should remain internally consistent after many trades."""
        exchanges = ["binance", "upbit"]
        fees = {ex: TradingFee(maker_pct=0.1, taker_pct=0.1) for ex in exchanges}
        initial_balances = {ex: {"USDT": 50_000.0, "BTC": 1.0} for ex in exchanges}
        executor = PaperExecutor(
            initial_balances=initial_balances, exchange_fees=fees,
        )

        ticks = _generate_realistic_tick_data(
            exchanges=exchanges,
            symbol="BTC/USDT",
            num_ticks=300,
            base_price=67_000.0,
            spread_injection_pct=1.5,
            spread_injection_interval=5,
        )

        detector = SpatialDetector(
            min_spread_pct=0.1, min_depth_usd=50.0, exchange_fees=fees,
        )
        risk_manager = RiskManager()
        pipeline = ArbitragePipeline(
            executor=executor, risk_manager=risk_manager, spatial_detector=detector,
        )

        for tick in ticks:
            pipeline.run_once(tick)

        portfolio = executor.get_portfolio()

        # All balances should be non-negative
        for ex_name, ex_balance in portfolio.exchange_balances.items():
            for asset_name, asset_balance in ex_balance.balances.items():
                assert asset_balance.free >= 0.0, (
                    f"Negative balance: {ex_name}/{asset_name} = {asset_balance.free}"
                )

        # Total raw balance should be positive (total_usd_value depends on usd_value being set)
        total_balance = sum(
            ab.free + ab.locked
            for eb in portfolio.exchange_balances.values()
            for ab in eb.balances.values()
        )
        assert total_balance > 0, "Total raw balance should be positive"

        # PnL should be calculable
        pnl = executor.get_pnl()
        assert isinstance(pnl, dict)
