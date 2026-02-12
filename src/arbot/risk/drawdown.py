"""Real-time drawdown monitoring.

Tracks the equity curve and detects when drawdown from peak equity
exceeds a configurable threshold, halting trading if necessary.
"""

from __future__ import annotations

from arbot.logging import get_logger

logger = get_logger(__name__)


class DrawdownMonitor:
    """Tracks equity curve and detects excessive drawdown.

    Maintains peak equity and current equity to compute real-time
    drawdown percentage. When drawdown exceeds the configured maximum,
    the monitor enters a halted state.

    Attributes:
        max_drawdown_pct: Maximum allowed drawdown percentage before halt.
    """

    def __init__(self, max_drawdown_pct: float = 5.0) -> None:
        """Initialize the drawdown monitor.

        Args:
            max_drawdown_pct: Maximum allowed drawdown as a percentage
                of peak equity. Trading is halted when exceeded.
        """
        self.max_drawdown_pct = max_drawdown_pct
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0
        self._is_halted: bool = False

    def update(self, equity: float) -> None:
        """Update with latest equity value.

        Tracks peak equity and triggers halt if drawdown exceeds threshold.

        Args:
            equity: Current total equity in USD.
        """
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        if self.current_drawdown_pct >= self.max_drawdown_pct:
            if not self._is_halted:
                logger.warning(
                    "drawdown_halt_triggered",
                    drawdown_pct=self.current_drawdown_pct,
                    peak_equity=self._peak_equity,
                    current_equity=self._current_equity,
                    threshold_pct=self.max_drawdown_pct,
                )
            self._is_halted = True

    def check(self) -> tuple[bool, str]:
        """Check if drawdown exceeds threshold.

        Returns:
            Tuple of (ok, reason). ok is True if drawdown is within limits.
        """
        if self._is_halted:
            return (
                False,
                f"drawdown {self.current_drawdown_pct:.2f}% exceeds max {self.max_drawdown_pct:.2f}%",
            )
        return True, "drawdown within limits"

    def reset(self) -> None:
        """Reset monitor state.

        Clears peak equity, current equity, and halted flag.
        """
        self._peak_equity = 0.0
        self._current_equity = 0.0
        self._is_halted = False
        logger.info("drawdown_monitor_reset")

    @property
    def current_drawdown_pct(self) -> float:
        """Current drawdown as a percentage of peak equity."""
        if self._peak_equity <= 0:
            return 0.0
        return ((self._peak_equity - self._current_equity) / self._peak_equity) * 100

    @property
    def peak_equity(self) -> float:
        """Highest observed equity value."""
        return self._peak_equity

    @property
    def is_halted(self) -> bool:
        """Whether trading has been halted due to excessive drawdown."""
        return self._is_halted
