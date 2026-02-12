"""Anomalous price movement detection.

Detects flash crashes, abnormally wide spreads, and stale order book
data that could lead to erroneous trades.
"""

from __future__ import annotations

import math
import time
from collections import deque

from arbot.logging import get_logger
from arbot.models.orderbook import OrderBook

logger = get_logger(__name__)


class AnomalyDetector:
    """Detects flash crashes, abnormal spreads, and stale prices.

    Maintains a rolling history of mid prices and spread values
    to compute statistical baselines for anomaly detection.

    Attributes:
        flash_crash_pct: Percentage drop threshold for flash crash detection.
        spread_std_threshold: Number of standard deviations from mean spread
            to flag as abnormal.
        stale_threshold_seconds: Maximum age of an order book before
            it is considered stale.
        history_size: Maximum number of historical data points to retain.
    """

    def __init__(
        self,
        flash_crash_pct: float = 10.0,
        spread_std_threshold: float = 3.0,
        stale_threshold_seconds: float = 30.0,
        history_size: int = 100,
    ) -> None:
        """Initialize the anomaly detector.

        Args:
            flash_crash_pct: Percentage drop in short window to flag
                as a flash crash.
            spread_std_threshold: Number of standard deviations from
                the mean spread to consider abnormal.
            stale_threshold_seconds: Seconds after which an order book
                timestamp is considered stale.
            history_size: Rolling window size for price/spread history.
        """
        self.flash_crash_pct = flash_crash_pct
        self.spread_std_threshold = spread_std_threshold
        self.stale_threshold_seconds = stale_threshold_seconds
        self.history_size = history_size
        self._price_history: deque[float] = deque(maxlen=history_size)
        self._spread_history: deque[float] = deque(maxlen=history_size)

    def update_history(self, orderbook: OrderBook) -> None:
        """Add orderbook data to price and spread history.

        Args:
            orderbook: Order book snapshot to record.
        """
        mid = orderbook.mid_price
        if mid > 0:
            self._price_history.append(mid)
        spread = orderbook.spread_pct
        self._spread_history.append(spread)

    def check_orderbook(self, orderbook: OrderBook) -> tuple[bool, str]:
        """Check an order book for anomalies.

        Runs flash crash, abnormal spread, and stale price checks.

        Args:
            orderbook: Order book snapshot to validate.

        Returns:
            Tuple of (ok, reason). ok is True if no anomalies detected.
        """
        flash = self._check_flash_crash(orderbook)
        if flash is not None:
            logger.warning(
                "anomaly_flash_crash",
                exchange=orderbook.exchange,
                symbol=orderbook.symbol,
                reason=flash,
            )
            return False, flash

        spread = self._check_abnormal_spread(orderbook)
        if spread is not None:
            logger.warning(
                "anomaly_abnormal_spread",
                exchange=orderbook.exchange,
                symbol=orderbook.symbol,
                reason=spread,
            )
            return False, spread

        stale = self._check_stale_price(orderbook)
        if stale is not None:
            logger.warning(
                "anomaly_stale_price",
                exchange=orderbook.exchange,
                symbol=orderbook.symbol,
                reason=stale,
            )
            return False, stale

        return True, "no anomalies detected"

    def _check_flash_crash(self, orderbook: OrderBook) -> str | None:
        """Check for flash crash by comparing current price to recent peak.

        Args:
            orderbook: Current order book snapshot.

        Returns:
            Error message if flash crash detected, None otherwise.
        """
        if len(self._price_history) < 2:
            return None

        mid = orderbook.mid_price
        if mid <= 0:
            return None

        recent_peak = max(self._price_history)
        if recent_peak <= 0:
            return None

        drop_pct = ((recent_peak - mid) / recent_peak) * 100
        if drop_pct >= self.flash_crash_pct:
            return (
                f"flash crash detected: price dropped {drop_pct:.2f}% "
                f"from recent peak {recent_peak:.2f} to {mid:.2f}"
            )
        return None

    def _check_abnormal_spread(self, orderbook: OrderBook) -> str | None:
        """Check if current spread is abnormally wide.

        Compares current spread to historical mean + N standard deviations.

        Args:
            orderbook: Current order book snapshot.

        Returns:
            Error message if abnormal spread detected, None otherwise.
        """
        if len(self._spread_history) < 2:
            return None

        current_spread = orderbook.spread_pct
        mean_spread = sum(self._spread_history) / len(self._spread_history)
        variance = sum(
            (s - mean_spread) ** 2 for s in self._spread_history
        ) / len(self._spread_history)
        std_spread = math.sqrt(variance)

        if std_spread <= 0:
            return None

        z_score = (current_spread - mean_spread) / std_spread
        if z_score >= self.spread_std_threshold:
            return (
                f"abnormal spread: {current_spread:.4f}% is {z_score:.2f} "
                f"std devs above mean {mean_spread:.4f}%"
            )
        return None

    def _check_stale_price(self, orderbook: OrderBook) -> str | None:
        """Check if the order book timestamp is too old.

        Args:
            orderbook: Current order book snapshot.

        Returns:
            Error message if stale price detected, None otherwise.
        """
        now = time.time()
        age = now - orderbook.timestamp
        if age > self.stale_threshold_seconds:
            return (
                f"stale price: order book is {age:.1f}s old "
                f"(threshold: {self.stale_threshold_seconds:.1f}s)"
            )
        return None
