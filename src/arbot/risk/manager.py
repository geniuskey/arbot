"""Risk manager for validating arbitrage signals before execution.

Checks position limits, daily loss limits, anomalous spread detection,
and circuit breaker conditions based on RiskConfig parameters.
"""

from datetime import datetime, timedelta

from arbot.models.balance import PortfolioSnapshot
from arbot.models.config import RiskConfig
from arbot.models.signal import ArbitrageSignal


class RiskManager:
    """Validates arbitrage signals against configurable risk parameters.

    Maintains internal state for daily PnL tracking and consecutive
    loss counting to support circuit breaker functionality.

    Attributes:
        config: Risk configuration parameters.
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        """Initialize the risk manager.

        Args:
            config: Risk configuration. Uses defaults if not provided.
        """
        self.config = config or RiskConfig()
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: datetime | None = None
        self._consecutive_losses: int = 0
        self._cooldown_until: datetime | None = None
        self._trade_count: int = 0

    def check_signal(
        self,
        signal: ArbitrageSignal,
        portfolio: PortfolioSnapshot,
    ) -> tuple[bool, str]:
        """Check whether a signal should be executed.

        Validates:
        1. Circuit breaker is not active (cooldown period).
        2. Position size does not exceed per-coin limit.
        3. Daily loss limit has not been reached.
        4. Spread is not anomalously large (possible bad data).
        5. Total portfolio exposure is within limits.

        Args:
            signal: The arbitrage signal to validate.
            portfolio: Current portfolio snapshot.

        Returns:
            Tuple of (approved, reason). approved is True if the signal
            passes all risk checks. reason describes why it was rejected
            or "approved" if it passed.
        """
        now = datetime.utcnow()

        # Reset daily PnL at the start of a new day
        if self._daily_pnl_date is None or self._daily_pnl_date.date() != now.date():
            self._daily_pnl = 0.0
            self._daily_pnl_date = now

        # 1. Circuit breaker: check cooldown
        if self._cooldown_until is not None and now < self._cooldown_until:
            return False, "circuit breaker cooldown active"

        # 2. Position size limit per coin
        trade_usd = signal.quantity * signal.buy_price
        if trade_usd > self.config.max_position_per_coin_usd:
            return False, f"position size {trade_usd:.2f} USD exceeds limit {self.config.max_position_per_coin_usd:.2f}"

        # 3. Daily loss limit
        if self._daily_pnl < -self.config.max_daily_loss_usd:
            return False, f"daily loss {self._daily_pnl:.2f} USD exceeds limit -{self.config.max_daily_loss_usd:.2f}"

        # 4. Anomalous spread detection
        if abs(signal.gross_spread_pct) > self.config.max_spread_pct:
            return False, f"spread {signal.gross_spread_pct:.4f}% exceeds max {self.config.max_spread_pct:.4f}%"

        # 5. Price deviation check
        if abs(signal.net_spread_pct) > self.config.price_deviation_threshold_pct:
            return False, f"net spread {signal.net_spread_pct:.4f}% exceeds deviation threshold {self.config.price_deviation_threshold_pct:.4f}%"

        # 6. Total exposure check
        total_exposure = portfolio.total_usd_value
        if total_exposure + trade_usd > self.config.max_total_exposure_usd:
            return False, f"total exposure {total_exposure + trade_usd:.2f} USD exceeds limit {self.config.max_total_exposure_usd:.2f}"

        return True, "approved"

    def record_trade(self, pnl: float) -> None:
        """Record a trade result for risk tracking.

        Updates daily PnL, consecutive loss counter, and triggers
        circuit breaker if needed.

        Args:
            pnl: Profit or loss in USD from the trade.
        """
        now = datetime.utcnow()

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

        # Trigger circuit breaker if consecutive loss limit reached
        if self._consecutive_losses >= self.config.consecutive_loss_limit:
            self._cooldown_until = now + timedelta(minutes=self.config.cooldown_minutes)
            self._consecutive_losses = 0

    def reset_daily(self) -> None:
        """Reset daily counters (PnL and date)."""
        self._daily_pnl = 0.0
        self._daily_pnl_date = datetime.utcnow()

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
        return datetime.utcnow() < self._cooldown_until

    @property
    def trade_count(self) -> int:
        """Total number of trades recorded."""
        return self._trade_count
