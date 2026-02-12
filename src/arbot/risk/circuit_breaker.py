"""Enhanced circuit breaker with graduated response.

Implements a state machine circuit breaker that transitions between
NORMAL, WARNING, TRIGGERED, and COOLDOWN states based on multiple
risk metrics.
"""

from __future__ import annotations

import time
from enum import Enum

from arbot.logging import get_logger

logger = get_logger(__name__)


class CircuitBreakerState(Enum):
    """Circuit breaker state machine states."""

    NORMAL = "normal"
    WARNING = "warning"
    TRIGGERED = "triggered"
    COOLDOWN = "cooldown"


class CircuitBreaker:
    """State machine circuit breaker with multiple trigger conditions.

    Transitions between states based on consecutive losses, daily loss,
    and drawdown metrics. Supports graduated response with position
    scaling in WARNING state.

    Attributes:
        max_consecutive_losses: Consecutive loss count to trigger.
        max_daily_loss_usd: Daily loss amount to trigger.
        max_drawdown_pct: Drawdown percentage to trigger.
        warning_threshold_pct: Percentage of limits that triggers WARNING.
        cooldown_seconds: Duration of cooldown period after trigger.
    """

    def __init__(
        self,
        max_consecutive_losses: int = 5,
        max_daily_loss_usd: float = 500.0,
        max_drawdown_pct: float = 5.0,
        warning_threshold_pct: float = 70.0,
        cooldown_seconds: float = 1800.0,
    ) -> None:
        """Initialize the circuit breaker.

        Args:
            max_consecutive_losses: Number of consecutive losses to trigger.
            max_daily_loss_usd: Daily loss in USD to trigger.
            max_drawdown_pct: Drawdown percentage to trigger.
            warning_threshold_pct: Percentage of any limit that triggers
                the WARNING state (0-100).
            cooldown_seconds: Seconds to remain in COOLDOWN before
                returning to NORMAL.
        """
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_loss_usd = max_daily_loss_usd
        self.max_drawdown_pct = max_drawdown_pct
        self.warning_threshold_pct = warning_threshold_pct
        self.cooldown_seconds = cooldown_seconds

        self._state = CircuitBreakerState.NORMAL
        self._triggered_at: float | None = None
        self._trigger_reason: str = ""

    @property
    def state(self) -> CircuitBreakerState:
        """Current circuit breaker state.

        Automatically transitions from COOLDOWN to NORMAL when
        the cooldown period expires.
        """
        if self._state == CircuitBreakerState.COOLDOWN:
            if self._triggered_at is not None:
                elapsed = time.monotonic() - self._triggered_at
                if elapsed >= self.cooldown_seconds:
                    self._state = CircuitBreakerState.NORMAL
                    self._triggered_at = None
                    self._trigger_reason = ""
                    logger.info("circuit_breaker_cooldown_expired")
        return self._state

    @property
    def can_trade(self) -> bool:
        """Whether trading is allowed in the current state.

        Returns True for NORMAL and WARNING states.
        """
        return self.state in (CircuitBreakerState.NORMAL, CircuitBreakerState.WARNING)

    @property
    def position_scale(self) -> float:
        """Position size scaling factor based on current state.

        Returns:
            1.0 for NORMAL, 0.5 for WARNING, 0.0 for TRIGGERED/COOLDOWN.
        """
        state = self.state
        if state == CircuitBreakerState.NORMAL:
            return 1.0
        if state == CircuitBreakerState.WARNING:
            return 0.5
        return 0.0

    def update(
        self,
        consecutive_losses: int = 0,
        daily_loss_usd: float = 0.0,
        drawdown_pct: float = 0.0,
    ) -> CircuitBreakerState:
        """Update state based on current risk metrics.

        Evaluates whether any metric has crossed its trigger or
        warning threshold and transitions state accordingly.

        Args:
            consecutive_losses: Current consecutive loss count.
            daily_loss_usd: Current daily loss in USD (positive value).
            drawdown_pct: Current drawdown percentage.

        Returns:
            The new circuit breaker state.
        """
        # Don't update if already triggered/cooldown - must wait for reset
        current = self.state
        if current in (CircuitBreakerState.TRIGGERED, CircuitBreakerState.COOLDOWN):
            return current

        new_state = self._evaluate_state(
            consecutive_losses, daily_loss_usd, drawdown_pct
        )

        if new_state != self._state:
            logger.info(
                "circuit_breaker_state_change",
                old_state=self._state.value,
                new_state=new_state.value,
                consecutive_losses=consecutive_losses,
                daily_loss_usd=daily_loss_usd,
                drawdown_pct=drawdown_pct,
            )

        if new_state == CircuitBreakerState.TRIGGERED:
            self._triggered_at = time.monotonic()
            self._trigger_reason = self._build_trigger_reason(
                consecutive_losses, daily_loss_usd, drawdown_pct
            )
            # Move to cooldown immediately after trigger
            self._state = CircuitBreakerState.COOLDOWN
            logger.warning(
                "circuit_breaker_triggered",
                reason=self._trigger_reason,
                cooldown_seconds=self.cooldown_seconds,
            )
            return CircuitBreakerState.COOLDOWN

        self._state = new_state
        return new_state

    def trigger(self, reason: str) -> None:
        """Manually trigger the circuit breaker.

        Args:
            reason: Human-readable reason for the manual trigger.
        """
        self._state = CircuitBreakerState.COOLDOWN
        self._triggered_at = time.monotonic()
        self._trigger_reason = reason
        logger.warning(
            "circuit_breaker_manual_trigger",
            reason=reason,
            cooldown_seconds=self.cooldown_seconds,
        )

    def reset(self) -> None:
        """Reset circuit breaker to NORMAL state."""
        self._state = CircuitBreakerState.NORMAL
        self._triggered_at = None
        self._trigger_reason = ""
        logger.info("circuit_breaker_reset")

    def _evaluate_state(
        self,
        consecutive_losses: int,
        daily_loss_usd: float,
        drawdown_pct: float,
    ) -> CircuitBreakerState:
        """Evaluate metrics and determine appropriate state.

        Args:
            consecutive_losses: Current consecutive loss count.
            daily_loss_usd: Current daily loss (positive = loss amount).
            drawdown_pct: Current drawdown percentage.

        Returns:
            The evaluated state based on current metrics.
        """
        # Check if any metric exceeds trigger threshold
        if consecutive_losses >= self.max_consecutive_losses:
            return CircuitBreakerState.TRIGGERED
        if daily_loss_usd >= self.max_daily_loss_usd:
            return CircuitBreakerState.TRIGGERED
        if drawdown_pct >= self.max_drawdown_pct:
            return CircuitBreakerState.TRIGGERED

        # Check if any metric exceeds warning threshold
        warning_factor = self.warning_threshold_pct / 100.0

        loss_ratio = consecutive_losses / self.max_consecutive_losses if self.max_consecutive_losses > 0 else 0
        daily_ratio = daily_loss_usd / self.max_daily_loss_usd if self.max_daily_loss_usd > 0 else 0
        dd_ratio = drawdown_pct / self.max_drawdown_pct if self.max_drawdown_pct > 0 else 0

        if loss_ratio >= warning_factor or daily_ratio >= warning_factor or dd_ratio >= warning_factor:
            return CircuitBreakerState.WARNING

        return CircuitBreakerState.NORMAL

    def _build_trigger_reason(
        self,
        consecutive_losses: int,
        daily_loss_usd: float,
        drawdown_pct: float,
    ) -> str:
        """Build a human-readable trigger reason string.

        Args:
            consecutive_losses: Current consecutive loss count.
            daily_loss_usd: Current daily loss.
            drawdown_pct: Current drawdown percentage.

        Returns:
            Descriptive reason string.
        """
        reasons: list[str] = []
        if consecutive_losses >= self.max_consecutive_losses:
            reasons.append(
                f"consecutive losses {consecutive_losses} >= {self.max_consecutive_losses}"
            )
        if daily_loss_usd >= self.max_daily_loss_usd:
            reasons.append(
                f"daily loss ${daily_loss_usd:.2f} >= ${self.max_daily_loss_usd:.2f}"
            )
        if drawdown_pct >= self.max_drawdown_pct:
            reasons.append(
                f"drawdown {drawdown_pct:.2f}% >= {self.max_drawdown_pct:.2f}%"
            )
        return "; ".join(reasons) if reasons else "unknown"
