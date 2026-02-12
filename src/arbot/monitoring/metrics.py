"""Prometheus metrics for ArBot system monitoring."""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    start_http_server,
)


class MetricsCollector:
    """Central Prometheus metrics registry for ArBot.

    Uses a custom CollectorRegistry to avoid global state conflicts,
    making it safe for use in tests and multiple instances.

    Attributes:
        registry: The Prometheus CollectorRegistry used for all metrics.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        """Initialize all Prometheus metrics.

        Args:
            registry: Custom registry. Creates a new one if not provided.
        """
        self._registry = registry or CollectorRegistry()

        # --- Counters ---
        self.signals_detected = Counter(
            "arbot_signals_detected_total",
            "Total arbitrage signals detected",
            ["strategy"],
            registry=self._registry,
        )
        self.signals_executed = Counter(
            "arbot_signals_executed_total",
            "Total signals executed",
            ["strategy"],
            registry=self._registry,
        )
        self.signals_rejected = Counter(
            "arbot_signals_rejected_total",
            "Total signals rejected",
            ["strategy", "reason"],
            registry=self._registry,
        )
        self.trades_total = Counter(
            "arbot_trades_total",
            "Total trades executed",
            ["exchange", "symbol", "side"],
            registry=self._registry,
        )
        self.cycles_total = Counter(
            "arbot_pipeline_cycles_total",
            "Total pipeline cycles run",
            registry=self._registry,
        )

        # --- Gauges ---
        self.current_pnl = Gauge(
            "arbot_current_pnl_usd",
            "Current P&L in USD",
            registry=self._registry,
        )
        self.current_drawdown = Gauge(
            "arbot_current_drawdown_pct",
            "Current drawdown percentage",
            registry=self._registry,
        )
        self.active_connections = Gauge(
            "arbot_active_connections",
            "Number of active exchange connections",
            ["exchange"],
            registry=self._registry,
        )
        self.portfolio_value = Gauge(
            "arbot_portfolio_value_usd",
            "Total portfolio value in USD",
            registry=self._registry,
        )
        self.spread_gauge = Gauge(
            "arbot_spread_pct",
            "Current spread percentage",
            ["pair"],
            registry=self._registry,
        )
        self.balance_gauge = Gauge(
            "arbot_balance_usd",
            "Balance in USD per exchange",
            ["exchange"],
            registry=self._registry,
        )
        self.risk_daily_pnl = Gauge(
            "arbot_risk_daily_pnl_usd",
            "Risk manager daily PnL",
            registry=self._registry,
        )
        self.risk_cooldown = Gauge(
            "arbot_risk_cooldown_active",
            "Whether risk cooldown is active (0 or 1)",
            registry=self._registry,
        )

        # --- Histograms ---
        self.trade_latency = Histogram(
            "arbot_trade_latency_seconds",
            "Trade execution latency",
            ["exchange"],
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
            registry=self._registry,
        )
        self.detection_latency = Histogram(
            "arbot_detection_latency_seconds",
            "Signal detection latency",
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1],
            registry=self._registry,
        )

        # --- Info ---
        self.system_info = Info(
            "arbot_system",
            "ArBot system information",
            registry=self._registry,
        )

    @property
    def registry(self) -> CollectorRegistry:
        """Return the collector registry."""
        return self._registry

    def record_signal(
        self, strategy: str, executed: bool, reject_reason: str = ""
    ) -> None:
        """Record an arbitrage signal event.

        Args:
            strategy: Strategy name (e.g. "spatial", "triangular").
            executed: Whether the signal was executed.
            reject_reason: Reason for rejection, if not executed.
        """
        self.signals_detected.labels(strategy=strategy).inc()
        if executed:
            self.signals_executed.labels(strategy=strategy).inc()
        elif reject_reason:
            self.signals_rejected.labels(
                strategy=strategy, reason=reject_reason
            ).inc()

    def record_trade(
        self, exchange: str, symbol: str, side: str, latency_ms: float
    ) -> None:
        """Record a trade execution.

        Args:
            exchange: Exchange name.
            symbol: Trading pair.
            side: "buy" or "sell".
            latency_ms: Execution latency in milliseconds.
        """
        self.trades_total.labels(
            exchange=exchange, symbol=symbol, side=side
        ).inc()
        self.trade_latency.labels(exchange=exchange).observe(
            latency_ms / 1000.0
        )

    def update_spread(self, pair: str, spread_pct: float) -> None:
        """Update the current spread for a pair.

        Args:
            pair: Trading pair identifier (e.g. "BTC/USDT:binance-upbit").
            spread_pct: Current spread percentage.
        """
        self.spread_gauge.labels(pair=pair).set(spread_pct)

    def update_balance(self, exchange: str, value_usd: float) -> None:
        """Update the balance for an exchange.

        Args:
            exchange: Exchange name.
            value_usd: Balance value in USD.
        """
        self.balance_gauge.labels(exchange=exchange).set(value_usd)

    def update_connection(self, exchange: str, connected: bool) -> None:
        """Update connection status for an exchange.

        Args:
            exchange: Exchange name.
            connected: Whether the exchange is connected.
        """
        self.active_connections.labels(exchange=exchange).set(
            1.0 if connected else 0.0
        )

    def update_risk_state(self, daily_pnl: float, in_cooldown: bool) -> None:
        """Update risk manager state metrics.

        Args:
            daily_pnl: Current daily PnL in USD.
            in_cooldown: Whether the circuit breaker cooldown is active.
        """
        self.risk_daily_pnl.set(daily_pnl)
        self.risk_cooldown.set(1.0 if in_cooldown else 0.0)

    def record_cycle(self) -> None:
        """Increment the pipeline cycle counter."""
        self.cycles_total.inc()

    def set_system_info(
        self, version: str, mode: str, exchanges: list[str]
    ) -> None:
        """Set system information labels.

        Args:
            version: Application version string.
            mode: Trading mode (e.g. "paper", "live").
            exchanges: List of configured exchange names.
        """
        self.system_info.info(
            {
                "version": version,
                "mode": mode,
                "exchanges": ",".join(exchanges),
            }
        )

    def start_server(self, port: int = 9090) -> None:
        """Start HTTP metrics server for Prometheus scraping.

        Args:
            port: Port number to listen on.
        """
        start_http_server(port, registry=self._registry)
