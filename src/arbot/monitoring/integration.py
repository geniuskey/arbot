"""Integration hooks for connecting metrics to ArBot components."""

from __future__ import annotations

from arbot.monitoring.metrics import MetricsCollector


class MetricsIntegration:
    """Hooks metrics collection into existing ArBot components.

    Provides convenience methods to update Prometheus metrics from
    ArBot domain objects like PipelineStats, PortfolioSnapshot,
    and RiskManager.

    Attributes:
        collector: The underlying MetricsCollector instance.
    """

    def __init__(self, collector: MetricsCollector) -> None:
        """Initialize the integration.

        Args:
            collector: MetricsCollector to push metrics to.
        """
        self.collector = collector

    def update_from_pipeline_stats(self, stats: object) -> None:
        """Update metrics from PipelineStats after each cycle.

        Args:
            stats: A PipelineStats instance with signal/PnL counters.
        """
        self.collector.current_pnl.set(getattr(stats, "total_pnl_usd", 0.0))

        total_detected = getattr(stats, "total_signals_detected", 0)
        total_executed = getattr(stats, "total_signals_executed", 0)
        cycles = getattr(stats, "cycles_run", 0)

        # Set counter values by syncing to the cumulative stats.
        # We use _value for direct set since PipelineStats tracks cumulatives.
        self.collector.signals_detected.labels(strategy="all")._value.set(
            total_detected
        )
        self.collector.signals_executed.labels(strategy="all")._value.set(
            total_executed
        )
        self.collector.cycles_total._value.set(cycles)

    def update_from_portfolio(self, portfolio: object) -> None:
        """Update balance metrics from PortfolioSnapshot.

        Args:
            portfolio: A PortfolioSnapshot with exchange_balances.
        """
        exchange_balances = getattr(portfolio, "exchange_balances", {})
        total_value = 0.0

        for name, eb in exchange_balances.items():
            usd_value = getattr(eb, "total_usd_value", 0.0)
            self.collector.update_balance(name, usd_value)
            total_value += usd_value

        self.collector.portfolio_value.set(total_value)

    def update_from_risk_manager(self, risk_manager: object) -> None:
        """Update risk metrics from RiskManager.

        Args:
            risk_manager: A RiskManager with daily_pnl and cooldown state.
        """
        daily_pnl = getattr(risk_manager, "daily_pnl", 0.0)
        in_cooldown = getattr(risk_manager, "is_in_cooldown", False)
        self.collector.update_risk_state(daily_pnl, in_cooldown)

    def record_detection_time(self, duration_seconds: float) -> None:
        """Record time taken for signal detection.

        Args:
            duration_seconds: Detection duration in seconds.
        """
        self.collector.detection_latency.observe(duration_seconds)

    def record_trade_execution(
        self,
        exchange: str,
        symbol: str,
        side: str,
        latency_ms: float,
    ) -> None:
        """Record trade execution metrics.

        Args:
            exchange: Exchange name.
            symbol: Trading pair.
            side: "buy" or "sell".
            latency_ms: Execution latency in milliseconds.
        """
        self.collector.record_trade(exchange, symbol, side, latency_ms)
