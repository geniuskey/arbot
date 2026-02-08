"""Discord notification channel for ArBot alerts.

Uses discord.py for rich Embed-based message delivery with retry logic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0

# Embed colors
COLOR_OPPORTUNITY = 0x3498DB  # Blue
COLOR_TRADE_SUCCESS = 0x2ECC71  # Green
COLOR_TRADE_FAIL = 0xE74C3C  # Red
COLOR_ERROR = 0xE74C3C  # Red
COLOR_SUMMARY = 0xF39C12  # Orange
COLOR_STATUS = 0x9B59B6  # Purple


class DiscordNotifier:
    """Sends formatted alert messages to Discord via Embed.

    Uses a two-phase initialization pattern:
    1. Constructor creates the notifier (no channel yet)
    2. set_channel() is called after bot connects and resolves the alert channel

    This allows the notifier to be created before the bot starts.
    """

    def __init__(self) -> None:
        self._channel: discord.TextChannel | None = None

    def set_channel(self, channel: discord.TextChannel) -> None:
        """Set the target channel for alert messages.

        Called from ArBotDiscord.on_ready() after the bot connects.

        Args:
            channel: Discord text channel to send alerts to.
        """
        self._channel = channel
        logger.info("Discord alert channel set: #%s", channel.name)

    async def send_message(self, text: str, **kwargs: Any) -> bool:
        """Send a message to the configured Discord channel.

        Retries up to MAX_RETRIES times on failure.

        Args:
            text: Message text to send.
            **kwargs: Optional 'embed' for rich formatting.

        Returns:
            True if message was sent successfully, False otherwise.
        """
        if self._channel is None:
            logger.warning("Discord channel not set, cannot send message")
            return False

        embed = kwargs.get("embed")

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if embed is not None:
                    await self._channel.send(content=text or None, embed=embed)
                else:
                    await self._channel.send(content=text)
                return True
            except discord.HTTPException as e:
                logger.error(
                    "Discord send failed (attempt %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    e,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
        return False

    def build_opportunity_embed(self, signal: Any) -> discord.Embed:
        """Build a rich Embed for an arbitrage opportunity.

        Args:
            signal: Arbitrage opportunity data.

        Returns:
            Discord Embed with opportunity details.
        """
        embed = discord.Embed(
            title="🔔 차익 기회 탐지",
            color=COLOR_OPPORTUNITY,
        )
        embed.add_field(name="심볼", value=f"`{signal.symbol}`", inline=True)
        embed.add_field(
            name="매수",
            value=f"{signal.buy_exchange}\n`${signal.buy_price:,.4f}`",
            inline=True,
        )
        embed.add_field(
            name="매도",
            value=f"{signal.sell_exchange}\n`${signal.sell_price:,.4f}`",
            inline=True,
        )
        embed.add_field(
            name="총 스프레드",
            value=f"`{signal.gross_spread_pct:.3f}%`",
            inline=True,
        )
        embed.add_field(
            name="순 스프레드",
            value=f"`{signal.net_spread_pct:.3f}%`",
            inline=True,
        )
        embed.add_field(
            name="예상 수익",
            value=f"`${signal.estimated_profit:,.2f}`",
            inline=True,
        )
        return embed

    def build_trade_result_embed(self, trade: Any) -> discord.Embed:
        """Build a rich Embed for a trade execution result.

        Args:
            trade: Trade result data.

        Returns:
            Discord Embed with trade details.
        """
        is_success = trade.status == "FILLED"
        color = COLOR_TRADE_SUCCESS if is_success else COLOR_TRADE_FAIL
        icon = "✅" if is_success else "⚠️"

        embed = discord.Embed(
            title=f"📊 거래 체결 결과 {icon}",
            color=color,
        )
        embed.add_field(name="상태", value=f"`{trade.status}`", inline=True)
        embed.add_field(name="거래소", value=trade.exchange, inline=True)
        embed.add_field(name="심볼", value=f"`{trade.symbol}`", inline=True)
        embed.add_field(name="방향", value=f"`{trade.side}`", inline=True)
        embed.add_field(
            name="체결량", value=f"`{trade.filled_qty:.6f}`", inline=True
        )
        embed.add_field(
            name="체결가", value=f"`${trade.filled_price:,.4f}`", inline=True
        )
        embed.add_field(
            name="수수료", value=f"`${trade.fee:,.6f}`", inline=True
        )
        embed.add_field(
            name="레이턴시", value=f"`{trade.latency_ms:.1f}ms`", inline=True
        )
        return embed

    def build_error_embed(self, error: Any) -> discord.Embed:
        """Build a rich Embed for an error alert.

        Args:
            error: Exception or error object.

        Returns:
            Discord Embed with error details.
        """
        embed = discord.Embed(
            title="🚨 에러 발생",
            color=COLOR_ERROR,
        )
        embed.add_field(
            name="유형", value=f"`{type(error).__name__}`", inline=False
        )
        embed.add_field(
            name="내용", value=f"`{error!s}`", inline=False
        )
        return embed

    # --- Notifier Protocol methods (plain text) ---

    def format_opportunity(self, signal: Any) -> str:
        """Format an arbitrage opportunity as plain text.

        Args:
            signal: Arbitrage opportunity data.

        Returns:
            Plain text formatted message.
        """
        return (
            f"🔔 차익 기회 탐지\n"
            f"심볼: {signal.symbol}\n"
            f"매수: {signal.buy_exchange} @ ${signal.buy_price:,.4f}\n"
            f"매도: {signal.sell_exchange} @ ${signal.sell_price:,.4f}\n"
            f"총 스프레드: {signal.gross_spread_pct:.3f}%\n"
            f"순 스프레드: {signal.net_spread_pct:.3f}%\n"
            f"예상 수익: ${signal.estimated_profit:,.2f}"
        )

    def format_trade_result(self, trade: Any) -> str:
        """Format a trade execution result as plain text.

        Args:
            trade: Trade result data.

        Returns:
            Plain text formatted message.
        """
        icon = "✅" if trade.status == "FILLED" else "⚠️"
        return (
            f"📊 거래 체결 결과 {icon}\n"
            f"상태: {trade.status}\n"
            f"거래소: {trade.exchange}\n"
            f"심볼: {trade.symbol}\n"
            f"방향: {trade.side}\n"
            f"체결량: {trade.filled_qty:.6f}\n"
            f"체결가: ${trade.filled_price:,.4f}\n"
            f"수수료: ${trade.fee:,.6f}\n"
            f"레이턴시: {trade.latency_ms:.1f}ms"
        )

    def format_daily_summary(self, stats: Any) -> str:
        """Format a daily PnL summary as plain text.

        Args:
            stats: Daily summary statistics.

        Returns:
            Plain text formatted message.
        """
        icon = "📈" if stats.net_pnl >= 0 else "📉"
        return (
            f"📋 일일 PnL 요약\n"
            f"날짜: {stats.date}\n"
            f"탐지 시그널: {stats.total_signals}\n"
            f"체결 거래: {stats.executed_trades}\n"
            f"총 PnL: ${stats.total_pnl:,.2f}\n"
            f"총 수수료: ${stats.total_fees:,.2f}\n"
            f"순 PnL: {icon} ${stats.net_pnl:,.2f}\n"
            f"승률: {stats.win_rate:.1%}\n"
            f"최대 DD: {stats.max_drawdown:.2%}"
        )

    def format_error(self, error: Any) -> str:
        """Format an error alert as plain text.

        Args:
            error: Exception or error object.

        Returns:
            Plain text formatted message.
        """
        return (
            f"🚨 에러 발생\n"
            f"유형: {type(error).__name__}\n"
            f"내용: {error!s}"
        )

    def format_system_status(self, status: Any) -> str:
        """Format a system status report as plain text.

        Args:
            status: System status data.

        Returns:
            Plain text formatted message.
        """
        exchanges = ", ".join(status.active_exchanges)
        return (
            f"🖥️ 시스템 상태\n"
            f"가동 시간: {status.uptime_hours:.1f}h\n"
            f"실행 모드: {status.execution_mode}\n"
            f"활성 거래소: {exchanges}\n"
            f"오픈 포지션: {status.open_positions}\n"
            f"총 잔고: ${status.total_balance_usd:,.2f}\n"
            f"CPU: {status.cpu_usage_pct:.1f}%\n"
            f"메모리: {status.memory_usage_pct:.1f}%"
        )
