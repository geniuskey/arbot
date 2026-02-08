"""Alert manager with throttling, deduplication, and priority handling.

Manages alert delivery through notification channels (Telegram, Discord, etc.)
with configurable rate limiting and duplicate suppression.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import deque
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field

from arbot.alerts.notifier_protocol import Notifier

logger = logging.getLogger(__name__)


class AlertPriority(IntEnum):
    """Alert priority levels (higher value = higher priority)."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


class AlertConfig(BaseModel):
    """Configuration for alert manager behavior.

    Attributes:
        throttle_intervals: Minimum seconds between same-type alerts.
        dedup_window_seconds: Time window for duplicate suppression.
        max_history: Maximum number of alert records to keep in history.
        critical_bypass_throttle: Whether CRITICAL alerts skip throttling.
    """

    throttle_intervals: dict[str, float] = Field(
        default_factory=lambda: {
            "opportunity": 30.0,
            "trade_result": 5.0,
            "daily_summary": 3600.0,
            "error": 60.0,
            "system_status": 300.0,
        },
    )
    dedup_window_seconds: float = 300.0
    max_history: int = 1000
    critical_bypass_throttle: bool = True


class AlertRecord(BaseModel):
    """Record of a sent alert for history tracking.

    Attributes:
        alert_type: Type/category of the alert.
        priority: Priority level of the alert.
        message: Alert message content.
        timestamp: Unix timestamp when alert was sent.
        delivered: Whether the alert was successfully delivered.
    """

    alert_type: str
    priority: AlertPriority
    message: str
    timestamp: float
    delivered: bool


class AlertManager:
    """Manages alert delivery with throttling and deduplication.

    Supports multiple notification channels (Telegram, Discord, etc.).
    Provides:
    - Priority-based alerting (CRITICAL, HIGH, MEDIUM, LOW)
    - Throttling: minimum interval between same-type alerts
    - Deduplication: suppress identical messages within a time window
    - History: retain recent N alert records
    - Fan-out: delivers to all configured notifiers

    Args:
        notifier: Single Notifier or list of Notifiers for message delivery.
        config: AlertConfig with throttling/dedup settings.
    """

    def __init__(
        self,
        notifier: Notifier | list[Notifier],
        config: AlertConfig | None = None,
    ) -> None:
        self._notifiers: list[Notifier] = (
            notifier if isinstance(notifier, list) else [notifier]
        )
        self._notifier = self._notifiers[0]
        self._config = config or AlertConfig()
        self._last_sent: dict[str, float] = {}
        self._dedup_hashes: dict[str, float] = {}
        self._history: deque[AlertRecord] = deque(maxlen=self._config.max_history)

    @property
    def history(self) -> list[AlertRecord]:
        """Return list of recent alert records."""
        return list(self._history)

    async def send_alert(
        self,
        alert_type: str,
        message: str,
        priority: AlertPriority = AlertPriority.MEDIUM,
    ) -> bool:
        """Send an alert through configured notification channels.

        Applies throttling and deduplication checks before sending.
        CRITICAL priority alerts bypass throttling when configured.

        Args:
            alert_type: Category of the alert (e.g., "opportunity", "error").
            message: Formatted message to send.
            priority: Alert priority level.

        Returns:
            True if alert was sent, False if suppressed or failed.
        """
        now = time.monotonic()

        # Check throttling (CRITICAL can bypass)
        bypass = (
            self._config.critical_bypass_throttle
            and priority >= AlertPriority.CRITICAL
        )
        if not bypass and self._is_throttled(alert_type, now):
            logger.debug("Alert throttled: type=%s", alert_type)
            return False

        # Check deduplication
        if self._is_duplicate(message, now):
            logger.debug("Alert deduplicated: type=%s", alert_type)
            return False

        # Send through all notification channels (fan-out)
        delivered = False
        for n in self._notifiers:
            try:
                if await n.send_message(message):
                    delivered = True
            except Exception:
                logger.exception("Notifier %s failed", type(n).__name__)

        # Record
        self._last_sent[alert_type] = now
        self._record_dedup(message, now)
        self._history.append(
            AlertRecord(
                alert_type=alert_type,
                priority=priority,
                message=message,
                timestamp=time.time(),
                delivered=delivered,
            ),
        )

        if delivered:
            logger.info("Alert sent: type=%s priority=%s", alert_type, priority.name)
        else:
            logger.error("Alert delivery failed: type=%s", alert_type)

        return delivered

    def _is_throttled(self, alert_type: str, now: float) -> bool:
        """Check if alert type is within its throttle interval.

        Args:
            alert_type: Type of alert to check.
            now: Current monotonic time.

        Returns:
            True if the alert should be suppressed due to throttling.
        """
        last = self._last_sent.get(alert_type)
        if last is None:
            return False
        interval = self._config.throttle_intervals.get(alert_type, 0.0)
        return (now - last) < interval

    def _is_duplicate(self, message: str, now: float) -> bool:
        """Check if an identical message was sent within the dedup window.

        Args:
            message: Message content to check for duplicates.
            now: Current monotonic time.

        Returns:
            True if a duplicate message exists within the window.
        """
        msg_hash = hashlib.md5(message.encode()).hexdigest()  # noqa: S324
        last_seen = self._dedup_hashes.get(msg_hash)
        if last_seen is None:
            return False
        return (now - last_seen) < self._config.dedup_window_seconds

    def _record_dedup(self, message: str, now: float) -> None:
        """Record a message hash for deduplication tracking.

        Also prunes expired entries from the dedup cache.

        Args:
            message: Message content to record.
            now: Current monotonic time.
        """
        msg_hash = hashlib.md5(message.encode()).hexdigest()  # noqa: S324
        self._dedup_hashes[msg_hash] = now

        # Prune expired entries
        expired = [
            h
            for h, t in self._dedup_hashes.items()
            if (now - t) >= self._config.dedup_window_seconds
        ]
        for h in expired:
            del self._dedup_hashes[h]

    def clear_throttle(self, alert_type: str | None = None) -> None:
        """Clear throttle state, allowing immediate alert delivery.

        Args:
            alert_type: Specific type to clear, or None to clear all.
        """
        if alert_type is None:
            self._last_sent.clear()
        else:
            self._last_sent.pop(alert_type, None)

    def clear_dedup(self) -> None:
        """Clear all deduplication hashes."""
        self._dedup_hashes.clear()
