"""Telegram notification bot for ArBot alerts.

Uses python-telegram-bot library for async message delivery with retry logic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import telegram

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0


class OpportunitySignal(Protocol):
    """Protocol for arbitrage opportunity signal data."""

    buy_exchange: str
    sell_exchange: str
    symbol: str
    buy_price: float
    sell_price: float
    gross_spread_pct: float
    net_spread_pct: float
    estimated_profit: float


class TradeResult(Protocol):
    """Protocol for trade execution result data."""

    signal_id: str
    exchange: str
    symbol: str
    side: str
    filled_qty: float
    filled_price: float
    fee: float
    status: str
    latency_ms: float


class DailySummaryStats(Protocol):
    """Protocol for daily PnL summary data."""

    date: str
    total_signals: int
    executed_trades: int
    total_pnl: float
    total_fees: float
    net_pnl: float
    win_rate: float
    max_drawdown: float


class SystemStatus(Protocol):
    """Protocol for system status data."""

    uptime_hours: float
    active_exchanges: list[str]
    execution_mode: str
    open_positions: int
    total_balance_usd: float
    cpu_usage_pct: float
    memory_usage_pct: float


def _escape_md(text: str) -> str:
    """Escape special characters for MarkdownV2 format.

    Args:
        text: Raw text to escape.

    Returns:
        Escaped text safe for MarkdownV2.
    """
    special_chars = r"_*[]()~`>#+-=|{}.!"
    escaped = []
    for char in str(text):
        if char in special_chars:
            escaped.append(f"\\{char}")
        else:
            escaped.append(char)
    return "".join(escaped)


class TelegramNotifier:
    """Sends formatted alert messages to Telegram.

    Uses python-telegram-bot for async delivery with automatic retry
    on connection failures (up to MAX_RETRIES attempts).

    Args:
        bot_token: Telegram Bot API token.
        chat_id: Target chat/channel ID for messages.
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot = telegram.Bot(token=bot_token)
        self._chat_id = chat_id

    async def send_message(
        self,
        text: str,
        parse_mode: str = "MarkdownV2",
    ) -> bool:
        """Send a message to the configured Telegram chat.

        Retries up to MAX_RETRIES times on failure with exponential backoff.

        Args:
            text: Message text to send.
            parse_mode: Telegram parse mode (default MarkdownV2).

        Returns:
            True if message was sent successfully, False otherwise.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode=parse_mode,
                )
                return True
            except telegram.error.RetryAfter as e:
                logger.warning(
                    "Telegram rate limited, retry after %s seconds (attempt %d/%d)",
                    e.retry_after,
                    attempt,
                    MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(e.retry_after)
            except telegram.error.TelegramError as e:
                logger.error(
                    "Telegram send failed (attempt %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    e,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
        return False

    def format_opportunity(self, signal: Any) -> str:
        """Format an arbitrage opportunity signal for Telegram.

        Args:
            signal: Arbitrage opportunity data (follows OpportunitySignal protocol).

        Returns:
            MarkdownV2 formatted message string.
        """
        return (
            f"*{_escape_md('🔔 차익 기회 탐지')}*\n\n"
            f"*심볼*: `{_escape_md(signal.symbol)}`\n"
            f"*매수*: {_escape_md(signal.buy_exchange)} "
            f"@ `{_escape_md(f'${signal.buy_price:,.4f}')}`\n"
            f"*매도*: {_escape_md(signal.sell_exchange)} "
            f"@ `{_escape_md(f'${signal.sell_price:,.4f}')}`\n"
            f"*총 스프레드*: `{_escape_md(f'{signal.gross_spread_pct:.3f}%')}`\n"
            f"*순 스프레드*: `{_escape_md(f'{signal.net_spread_pct:.3f}%')}`\n"
            f"*예상 수익*: `{_escape_md(f'${signal.estimated_profit:,.2f}')}`"
        )

    def format_trade_result(self, trade: Any) -> str:
        """Format a trade execution result for Telegram.

        Args:
            trade: Trade result data (follows TradeResult protocol).

        Returns:
            MarkdownV2 formatted message string.
        """
        status_icon = (
            _escape_md("✅") if trade.status == "FILLED" else _escape_md("⚠️")
        )
        return (
            f"*{_escape_md('📊 거래 체결 결과')}*\n\n"
            f"*상태*: {status_icon} `{_escape_md(trade.status)}`\n"
            f"*거래소*: {_escape_md(trade.exchange)}\n"
            f"*심볼*: `{_escape_md(trade.symbol)}`\n"
            f"*방향*: `{_escape_md(trade.side)}`\n"
            f"*체결량*: `{_escape_md(f'{trade.filled_qty:.6f}')}`\n"
            f"*체결가*: `{_escape_md(f'${trade.filled_price:,.4f}')}`\n"
            f"*수수료*: `{_escape_md(f'${trade.fee:,.6f}')}`\n"
            f"*레이턴시*: `{_escape_md(f'{trade.latency_ms:.1f}ms')}`"
        )

    def format_daily_summary(self, stats: Any) -> str:
        """Format a daily PnL summary for Telegram.

        Args:
            stats: Daily summary statistics (follows DailySummaryStats protocol).

        Returns:
            MarkdownV2 formatted message string.
        """
        pnl_icon = _escape_md("📈") if stats.net_pnl >= 0 else _escape_md("📉")
        return (
            f"*{_escape_md('📋 일일 PnL 요약')}*\n\n"
            f"*날짜*: `{_escape_md(stats.date)}`\n"
            f"*탐지 시그널*: `{_escape_md(str(stats.total_signals))}`\n"
            f"*체결 거래*: `{_escape_md(str(stats.executed_trades))}`\n"
            f"*총 PnL*: `{_escape_md(f'${stats.total_pnl:,.2f}')}`\n"
            f"*총 수수료*: `{_escape_md(f'${stats.total_fees:,.2f}')}`\n"
            f"*순 PnL*: {pnl_icon} `{_escape_md(f'${stats.net_pnl:,.2f}')}`\n"
            f"*승률*: `{_escape_md(f'{stats.win_rate:.1%}')}`\n"
            f"*최대 DD*: `{_escape_md(f'{stats.max_drawdown:.2%}')}`"
        )

    def format_error(self, error: Any) -> str:
        """Format an error alert for Telegram.

        Args:
            error: Exception or error object with string representation.

        Returns:
            MarkdownV2 formatted message string.
        """
        error_type = _escape_md(type(error).__name__)
        error_msg = _escape_md(str(error))
        return (
            f"*{_escape_md('🚨 에러 발생')}*\n\n"
            f"*유형*: `{error_type}`\n"
            f"*내용*: `{error_msg}`"
        )

    def format_system_status(self, status: Any) -> str:
        """Format a system status report for Telegram.

        Args:
            status: System status data (follows SystemStatus protocol).

        Returns:
            MarkdownV2 formatted message string.
        """
        exchanges = ", ".join(status.active_exchanges)
        return (
            f"*{_escape_md('🖥️ 시스템 상태')}*\n\n"
            f"*가동 시간*: `{_escape_md(f'{status.uptime_hours:.1f}h')}`\n"
            f"*실행 모드*: `{_escape_md(status.execution_mode)}`\n"
            f"*활성 거래소*: `{_escape_md(exchanges)}`\n"
            f"*오픈 포지션*: `{_escape_md(str(status.open_positions))}`\n"
            f"*총 잔고*: `{_escape_md(f'${status.total_balance_usd:,.2f}')}`\n"
            f"*CPU*: `{_escape_md(f'{status.cpu_usage_pct:.1f}%')}`\n"
            f"*메모리*: `{_escape_md(f'{status.memory_usage_pct:.1f}%')}`"
        )
