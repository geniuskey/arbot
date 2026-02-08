"""Notifier protocol for multi-channel alert delivery."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Protocol defining the notification channel interface.

    Any notifier (Telegram, Discord, etc.) must implement these methods.
    Existing TelegramNotifier already satisfies this protocol without changes.
    """

    async def send_message(self, text: str, **kwargs: Any) -> bool:
        """Send a message through this notification channel.

        Args:
            text: Message text to send.
            **kwargs: Channel-specific options (e.g., parse_mode, embed).

        Returns:
            True if sent successfully, False otherwise.
        """
        ...

    def format_opportunity(self, signal: Any) -> str:
        """Format an arbitrage opportunity signal.

        Args:
            signal: Arbitrage opportunity data.

        Returns:
            Formatted message string.
        """
        ...

    def format_trade_result(self, trade: Any) -> str:
        """Format a trade execution result.

        Args:
            trade: Trade result data.

        Returns:
            Formatted message string.
        """
        ...

    def format_daily_summary(self, stats: Any) -> str:
        """Format a daily PnL summary.

        Args:
            stats: Daily summary statistics.

        Returns:
            Formatted message string.
        """
        ...

    def format_error(self, error: Any) -> str:
        """Format an error alert.

        Args:
            error: Exception or error object.

        Returns:
            Formatted message string.
        """
        ...

    def format_system_status(self, status: Any) -> str:
        """Format a system status report.

        Args:
            status: System status data.

        Returns:
            Formatted message string.
        """
        ...
