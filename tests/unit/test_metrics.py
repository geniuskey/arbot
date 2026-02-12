"""Tests for arbot.monitoring metrics and integration."""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry

from arbot.models.balance import (
    AssetBalance,
    ExchangeBalance,
    PortfolioSnapshot,
)
from arbot.monitoring.integration import MetricsIntegration
from arbot.monitoring.metrics import MetricsCollector


@dataclass
class _FakePipelineStats:
    """Lightweight stand-in for PipelineStats to avoid circular imports."""

    total_signals_detected: int = 0
    total_signals_approved: int = 0
    total_signals_rejected: int = 0
    total_signals_executed: int = 0
    total_signals_failed: int = 0
    total_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    cycles_run: int = 0


class _FakeRiskManager:
    """Lightweight stand-in for RiskManager to avoid circular imports."""

    def __init__(self, daily_pnl: float = 0.0, is_in_cooldown: bool = False) -> None:
        self.daily_pnl = daily_pnl
        self.is_in_cooldown = is_in_cooldown


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    def test_counter_signals_detected(self) -> None:
        """Signal detected counter increments correctly."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.signals_detected.labels(strategy="spatial").inc()
        mc.signals_detected.labels(strategy="spatial").inc()
        mc.signals_detected.labels(strategy="triangular").inc()

        assert mc.signals_detected.labels(strategy="spatial")._value.get() == 2.0
        assert mc.signals_detected.labels(strategy="triangular")._value.get() == 1.0

    def test_counter_trades_total(self) -> None:
        """Trades counter increments with labels."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.trades_total.labels(exchange="binance", symbol="BTC/USDT", side="buy").inc()
        mc.trades_total.labels(exchange="binance", symbol="BTC/USDT", side="buy").inc()
        mc.trades_total.labels(exchange="upbit", symbol="BTC/USDT", side="sell").inc()

        assert (
            mc.trades_total.labels(
                exchange="binance", symbol="BTC/USDT", side="buy"
            )._value.get()
            == 2.0
        )
        assert (
            mc.trades_total.labels(
                exchange="upbit", symbol="BTC/USDT", side="sell"
            )._value.get()
            == 1.0
        )

    def test_gauge_current_pnl(self) -> None:
        """PnL gauge sets and updates correctly."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.current_pnl.set(150.5)
        assert mc.current_pnl._value.get() == 150.5

        mc.current_pnl.set(-20.0)
        assert mc.current_pnl._value.get() == -20.0

    def test_gauge_balance(self) -> None:
        """Balance gauge updates per exchange."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.update_balance("binance", 10000.0)
        mc.update_balance("upbit", 5000.0)

        assert mc.balance_gauge.labels(exchange="binance")._value.get() == 10000.0
        assert mc.balance_gauge.labels(exchange="upbit")._value.get() == 5000.0

    def test_gauge_connection(self) -> None:
        """Connection gauge toggles between 0 and 1."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.update_connection("binance", True)
        assert mc.active_connections.labels(exchange="binance")._value.get() == 1.0

        mc.update_connection("binance", False)
        assert mc.active_connections.labels(exchange="binance")._value.get() == 0.0

    def test_histogram_trade_latency(self) -> None:
        """Trade latency histogram records observations."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.trade_latency.labels(exchange="binance").observe(0.05)
        mc.trade_latency.labels(exchange="binance").observe(0.10)

        # Histogram sum should be ~0.15
        assert abs(mc.trade_latency.labels(exchange="binance")._sum.get() - 0.15) < 1e-9

    def test_histogram_detection_latency(self) -> None:
        """Detection latency histogram records observations."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.detection_latency.observe(0.005)
        mc.detection_latency.observe(0.010)

        assert mc.detection_latency._sum.get() == 0.015

    def test_system_info(self) -> None:
        """System info is set correctly."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.set_system_info("1.0.0", "paper", ["binance", "upbit"])

        # Info metric stores as {name}_{key} gauge
        # Verify the info was set without error
        assert mc.system_info is not None

    def test_record_signal_executed(self) -> None:
        """record_signal increments detected and executed counters."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.record_signal("spatial", executed=True)

        assert mc.signals_detected.labels(strategy="spatial")._value.get() == 1.0
        assert mc.signals_executed.labels(strategy="spatial")._value.get() == 1.0

    def test_record_signal_rejected(self) -> None:
        """record_signal increments detected and rejected counters."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.record_signal("spatial", executed=False, reject_reason="position_limit")

        assert mc.signals_detected.labels(strategy="spatial")._value.get() == 1.0
        assert (
            mc.signals_rejected.labels(
                strategy="spatial", reason="position_limit"
            )._value.get()
            == 1.0
        )

    def test_record_trade(self) -> None:
        """record_trade increments counter and records latency."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.record_trade("binance", "BTC/USDT", "buy", latency_ms=50.0)

        assert (
            mc.trades_total.labels(
                exchange="binance", symbol="BTC/USDT", side="buy"
            )._value.get()
            == 1.0
        )
        # 50ms = 0.05s
        assert mc.trade_latency.labels(exchange="binance")._sum.get() == 0.05

    def test_update_spread(self) -> None:
        """update_spread sets the spread gauge."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.update_spread("BTC/USDT:binance-upbit", 0.35)

        assert (
            mc.spread_gauge.labels(pair="BTC/USDT:binance-upbit")._value.get()
            == 0.35
        )

    def test_update_risk_state(self) -> None:
        """update_risk_state sets risk gauges."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.update_risk_state(daily_pnl=-50.0, in_cooldown=True)

        assert mc.risk_daily_pnl._value.get() == -50.0
        assert mc.risk_cooldown._value.get() == 1.0

    def test_record_cycle(self) -> None:
        """record_cycle increments cycles counter."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)

        mc.record_cycle()
        mc.record_cycle()
        mc.record_cycle()

        assert mc.cycles_total._value.get() == 3.0

    def test_custom_registry_isolation(self) -> None:
        """Two collectors with different registries are independent."""
        reg1 = CollectorRegistry()
        reg2 = CollectorRegistry()

        mc1 = MetricsCollector(registry=reg1)
        mc2 = MetricsCollector(registry=reg2)

        mc1.current_pnl.set(100.0)
        mc2.current_pnl.set(-50.0)

        assert mc1.current_pnl._value.get() == 100.0
        assert mc2.current_pnl._value.get() == -50.0

    def test_registry_property(self) -> None:
        """registry property returns the internal registry."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        assert mc.registry is registry

    def test_default_registry_created(self) -> None:
        """A new registry is created when none is provided."""
        mc = MetricsCollector()
        assert mc.registry is not None
        assert isinstance(mc.registry, CollectorRegistry)


class TestMetricsIntegration:
    """Tests for MetricsIntegration."""

    def test_update_from_pipeline_stats(self) -> None:
        """Pipeline stats are correctly mapped to metrics."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        integration = MetricsIntegration(mc)

        stats = _FakePipelineStats(
            total_signals_detected=10,
            total_signals_approved=8,
            total_signals_rejected=2,
            total_signals_executed=7,
            total_signals_failed=1,
            total_pnl_usd=250.0,
            total_fees_usd=5.0,
            cycles_run=15,
        )

        integration.update_from_pipeline_stats(stats)

        assert mc.current_pnl._value.get() == 250.0
        assert mc.signals_detected.labels(strategy="all")._value.get() == 10.0
        assert mc.signals_executed.labels(strategy="all")._value.get() == 7.0
        assert mc.cycles_total._value.get() == 15.0

    def test_update_from_portfolio(self) -> None:
        """Portfolio snapshot updates balance and portfolio value gauges."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        integration = MetricsIntegration(mc)

        portfolio = PortfolioSnapshot(
            exchange_balances={
                "binance": ExchangeBalance(
                    exchange="binance",
                    balances={
                        "USDT": AssetBalance(
                            asset="USDT", free=10000.0, usd_value=10000.0
                        ),
                        "BTC": AssetBalance(
                            asset="BTC", free=0.5, usd_value=25000.0
                        ),
                    },
                ),
                "upbit": ExchangeBalance(
                    exchange="upbit",
                    balances={
                        "USDT": AssetBalance(
                            asset="USDT", free=5000.0, usd_value=5000.0
                        ),
                    },
                ),
            }
        )

        integration.update_from_portfolio(portfolio)

        assert mc.balance_gauge.labels(exchange="binance")._value.get() == 35000.0
        assert mc.balance_gauge.labels(exchange="upbit")._value.get() == 5000.0
        assert mc.portfolio_value._value.get() == 40000.0

    def test_update_from_risk_manager(self) -> None:
        """Risk manager state updates risk gauges."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        integration = MetricsIntegration(mc)

        rm = _FakeRiskManager(daily_pnl=-15.0, is_in_cooldown=False)

        integration.update_from_risk_manager(rm)

        assert mc.risk_daily_pnl._value.get() == -15.0
        assert mc.risk_cooldown._value.get() == 0.0

    def test_update_from_risk_manager_in_cooldown(self) -> None:
        """Risk manager in cooldown sets cooldown gauge to 1."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        integration = MetricsIntegration(mc)

        rm = _FakeRiskManager(daily_pnl=-50.0, is_in_cooldown=True)

        integration.update_from_risk_manager(rm)

        assert mc.risk_cooldown._value.get() == 1.0
        assert mc.risk_daily_pnl._value.get() == -50.0

    def test_record_detection_time(self) -> None:
        """Detection time is recorded in the histogram."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        integration = MetricsIntegration(mc)

        integration.record_detection_time(0.025)

        assert mc.detection_latency._sum.get() == 0.025

    def test_record_trade_execution(self) -> None:
        """Trade execution records counter and latency."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        integration = MetricsIntegration(mc)

        integration.record_trade_execution("binance", "BTC/USDT", "buy", 75.0)

        assert (
            mc.trades_total.labels(
                exchange="binance", symbol="BTC/USDT", side="buy"
            )._value.get()
            == 1.0
        )
        # 75ms = 0.075s
        assert mc.trade_latency.labels(exchange="binance")._sum.get() == 0.075

    def test_update_from_empty_portfolio(self) -> None:
        """Empty portfolio sets portfolio value to 0."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        integration = MetricsIntegration(mc)

        portfolio = PortfolioSnapshot()

        integration.update_from_portfolio(portfolio)

        assert mc.portfolio_value._value.get() == 0.0

    def test_multiple_pipeline_updates(self) -> None:
        """Multiple pipeline stat updates overwrite correctly."""
        registry = CollectorRegistry()
        mc = MetricsCollector(registry=registry)
        integration = MetricsIntegration(mc)

        stats1 = _FakePipelineStats(
            total_signals_detected=5,
            total_signals_executed=3,
            total_pnl_usd=100.0,
            cycles_run=5,
        )
        integration.update_from_pipeline_stats(stats1)

        stats2 = _FakePipelineStats(
            total_signals_detected=12,
            total_signals_executed=9,
            total_pnl_usd=350.0,
            cycles_run=10,
        )
        integration.update_from_pipeline_stats(stats2)

        assert mc.current_pnl._value.get() == 350.0
        assert mc.signals_detected.labels(strategy="all")._value.get() == 12.0
        assert mc.cycles_total._value.get() == 10.0
