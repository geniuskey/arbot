"""Risk manager for validating arbitrage signals before execution.

Checks position limits, daily loss limits, anomalous spread detection,
and circuit breaker conditions based on RiskConfig parameters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from arbot.models.balance import PortfolioSnapshot
from arbot.models.config import RiskConfig
from arbot.models.orderbook import OrderBook
from arbot.models.signal import ArbitrageSignal
from arbot.risk.anomaly_detector import AnomalyDetector
from arbot.risk.circuit_breaker import CircuitBreaker
from arbot.risk.drawdown import DrawdownMonitor


class RiskManager:
    """Validates arbitrage signals against configurable risk parameters.

    Maintains internal state for daily PnL tracking and consecutive
    loss counting to support circuit breaker functionality. Optionally
    integrates DrawdownMonitor, AnomalyDetector, and CircuitBreaker
    for enhanced risk management.

    Attributes:
        config: Risk configuration parameters.
        drawdown_monitor: Optional drawdown monitor instance.
        anomaly_detector: Optional anomaly detector instance.
        circuit_breaker: Optional enhanced circuit breaker instance.
    """

    def __init__(
        self,
        config: RiskConfig | None = None,
        drawdown_monitor: DrawdownMonitor | None = None,
        anomaly_detector: AnomalyDetector | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        """Initialize the risk manager.

        Args:
            config: Risk configuration. Uses defaults if not provided.
            drawdown_monitor: Optional drawdown monitor for equity tracking.
            anomaly_detector: Optional anomaly detector for price validation.
            circuit_breaker: Optional enhanced circuit breaker.
        """
        self.config = config or RiskConfig()
        self.drawdown_monitor = drawdown_monitor
        self.anomaly_detector = anomaly_detector
        self.circuit_breaker = circuit_breaker
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: datetime | None = None
        self._consecutive_losses: int = 0
        self._cooldown_until: datetime | None = None
        self._trade_count: int = 0

    def check_signal(
        self,
        signal: ArbitrageSignal,
        portfolio: PortfolioSnapshot,
        orderbooks: dict[str, OrderBook] | None = None,
    ) -> tuple[bool, str]:
        """Check whether a signal should be executed.

        Validates:
        1. Enhanced circuit breaker (if present).
        2. Circuit breaker is not active (cooldown period).
        3. Drawdown monitor (if present).
        4. Anomaly detector on relevant order books (if present).
        5. Position size does not exceed per-coin limit.
        6. Daily loss limit has not been reached.
        7. Spread is not anomalously large (possible bad data).
        8. Total portfolio exposure is within limits.

        Args:
            signal: The arbitrage signal to validate.
            portfolio: Current portfolio snapshot.
            orderbooks: Optional dict of exchange name to OrderBook
                for anomaly detection.

        Returns:
            Tuple of (approved, reason). approved is True if the signal
            passes all risk checks. reason describes why it was rejected
            or "approved" if it passed.
        """
        now = datetime.now(UTC)

        # Reset daily PnL at the start of a new day
        if self._daily_pnl_date is None or self._daily_pnl_date.date() != now.date():
            self._daily_pnl = 0.0
            self._daily_pnl_date = now

        # 1. Enhanced circuit breaker check
        if self.circuit_breaker is not None and not self.circuit_breaker.can_trade:
            return False, "enhanced circuit breaker active"

        # 2. Legacy circuit breaker: check cooldown
        if self._cooldown_until is not None and now < self._cooldown_until:
            return False, "circuit breaker cooldown active"

        # 3. Drawdown monitor check
        if self.drawdown_monitor is not None:
            ok, reason = self.drawdown_monitor.check()
            if not ok:
                return False, reason

        # 4. Anomaly detector on relevant order books
        if self.anomaly_detector is not None and orderbooks is not None:
            for exchange_name in (signal.buy_exchange, signal.sell_exchange):
                ob = orderbooks.get(exchange_name)
                if ob is not None:
                    ok, reason = self.anomaly_detector.check_orderbook(ob)
                    if not ok:
                        return False, reason

        # 5. Position size limit per coin
        trade_usd = signal.quantity * signal.buy_price
        if trade_usd > self.config.max_position_per_coin_usd:
            return False, f"position size {trade_usd:.2f} USD exceeds limit {self.config.max_position_per_coin_usd:.2f}"

        # 6. Daily loss limit
        if self._daily_pnl < -self.config.max_daily_loss_usd:
            return False, f"daily loss {self._daily_pnl:.2f} USD exceeds limit -{self.config.max_daily_loss_usd:.2f}"

        # 7. Anomalous spread detection
        if abs(signal.gross_spread_pct) > self.config.max_spread_pct:
            return False, f"spread {signal.gross_spread_pct:.4f}% exceeds max {self.config.max_spread_pct:.4f}%"

        # Price deviation check
        if abs(signal.net_spread_pct) > self.config.price_deviation_threshold_pct:
            return False, f"net spread {signal.net_spread_pct:.4f}% exceeds deviation threshold {self.config.price_deviation_threshold_pct:.4f}%"

        # 8. Total exposure check
        total_exposure = portfolio.total_usd_value
        if total_exposure + trade_usd > self.config.max_total_exposure_usd:
            return False, f"total exposure {total_exposure + trade_usd:.2f} USD exceeds limit {self.config.max_total_exposure_usd:.2f}"

        return True, "approved"

    def record_trade(self, pnl: float, equity: float | None = None) -> None:
        """Record a trade result for risk tracking.

        Updates daily PnL, consecutive loss counter, and triggers
        circuit breaker if needed. Also updates drawdown monitor
        and enhanced circuit breaker if present.

        Args:
            pnl: Profit or loss in USD from the trade.
            equity: Optional current total equity for drawdown tracking.
        """
        now = datetime.now(UTC)

        # Reset daily PnL at the start of a new day
        if self._daily_pnl_date is None or self._daily_pnl_date.date() != now.date():
            self._daily_pnl = 0.0
            self._daily_pnl_date = now

        self._daily_pnl += pnl
        self._trade_count += 1

        # Track consecutive losses
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # Trigger legacy circuit breaker if consecutive loss limit reached
        if self._consecutive_losses >= self.config.consecutive_loss_limit:
            self._cooldown_until = now + timedelta(minutes=self.config.cooldown_minutes)
            self._consecutive_losses = 0

        # Update drawdown monitor
        if self.drawdown_monitor is not None and equity is not None:
            self.drawdown_monitor.update(equity)

        # Update enhanced circuit breaker
        if self.circuit_breaker is not None:
            dd_pct = 0.0
            if self.drawdown_monitor is not None:
                dd_pct = self.drawdown_monitor.current_drawdown_pct
            self.circuit_breaker.update(
                consecutive_losses=self._consecutive_losses,
                daily_loss_usd=abs(self._daily_pnl) if self._daily_pnl < 0 else 0.0,
                drawdown_pct=dd_pct,
            )

    def reset_daily(self) -> None:
        """Reset daily counters (PnL and date)."""
        self._daily_pnl = 0.0
        self._daily_pnl_date = datetime.now(UTC)

    @property
    def daily_pnl(self) -> float:
        """Current daily PnL in USD."""
        return self._daily_pnl

    @property
    def consecutive_losses(self) -> int:
        """Current consecutive loss count."""
        return self._consecutive_losses

    @property
    def is_in_cooldown(self) -> bool:
        """Whether the circuit breaker cooldown is active."""
        if self._cooldown_until is None:
            return False
        return datetime.now(UTC) < self._cooldown_until

    @property
    def trade_count(self) -> int:
        """Total number of trades recorded."""
        return self._trade_count
