"""Unit tests for DiscordNotifier."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import discord
import pytest

from arbot.alerts.discord_notifier import (
    COLOR_ERROR,
    COLOR_OPPORTUNITY,
    COLOR_TRADE_FAIL,
    COLOR_TRADE_SUCCESS,
    DiscordNotifier,
)


# ---------------------------------------------------------------------------
# Fake data fixtures (same pattern as test_alerts.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeSignal:
    buy_exchange: str = "binance"
    sell_exchange: str = "upbit"
    symbol: str = "BTC/USDT"
    buy_price: float = 67_000.1234
    sell_price: float = 67_500.5678
    gross_spread_pct: float = 0.747
    net_spread_pct: float = 0.547
    estimated_profit: float = 52.31


@dataclass
class FakeTrade:
    signal_id: str = "abc-123"
    exchange: str = "binance"
    symbol: str = "ETH/USDT"
    side: str = "BUY"
    filled_qty: float = 1.5
    filled_price: float = 3_400.1234
    fee: float = 1.7
    status: str = "FILLED"
    latency_ms: float = 45.2


@dataclass
class FakeDailySummary:
    date: str = "2026-02-08"
    total_signals: int = 142
    executed_trades: int = 38
    total_pnl: float = 523.45
    total_fees: float = 87.12
    net_pnl: float = 436.33
    win_rate: float = 0.789
    max_drawdown: float = 0.023


@dataclass
class FakeSystemStatus:
    uptime_hours: float = 12.5
    active_exchanges: list[str] | None = None
    execution_mode: str = "paper"
    open_positions: int = 3
    total_balance_usd: float = 50_000.0
    cpu_usage_pct: float = 32.1
    memory_usage_pct: float = 58.4

    def __post_init__(self) -> None:
        if self.active_exchanges is None:
            self.active_exchanges = ["binance", "okx", "bybit"]


# ---------------------------------------------------------------------------
# DiscordNotifier tests
# ---------------------------------------------------------------------------


class TestDiscordNotifierChannel:
    """Tests for channel management."""

    def test_initial_channel_is_none(self) -> None:
        notifier = DiscordNotifier()
        assert notifier._channel is None

    def test_set_channel(self) -> None:
        notifier = DiscordNotifier()
        mock_channel = MagicMock(spec=discord.TextChannel)
        type(mock_channel).name = PropertyMock(return_value="alerts")
        notifier.set_channel(mock_channel)
        assert notifier._channel is mock_channel


class TestDiscordNotifierSendMessage:
    """Tests for send_message method."""

    @pytest.fixture
    def notifier(self) -> DiscordNotifier:
        n = DiscordNotifier()
        mock_channel = AsyncMock(spec=discord.TextChannel)
        type(mock_channel).name = PropertyMock(return_value="alerts")
        n.set_channel(mock_channel)
        return n

    async def test_send_message_no_channel(self) -> None:
        notifier = DiscordNotifier()
        result = await notifier.send_message("test")
        assert result is False

    async def test_send_message_success(self, notifier: DiscordNotifier) -> None:
        result = await notifier.send_message("hello")
        assert result is True
        notifier._channel.send.assert_called_once_with(content="hello")

    async def test_send_message_with_embed(self, notifier: DiscordNotifier) -> None:
        embed = discord.Embed(title="test")
        result = await notifier.send_message("", embed=embed)
        assert result is True
        notifier._channel.send.assert_called_once_with(content=None, embed=embed)

    async def test_send_message_retries_on_failure(
        self, notifier: DiscordNotifier
    ) -> None:
        notifier._channel.send.side_effect = [
            discord.HTTPException(MagicMock(), "error"),
            discord.HTTPException(MagicMock(), "error"),
            MagicMock(),
        ]
        result = await notifier.send_message("test")
        assert result is True
        assert notifier._channel.send.call_count == 3

    async def test_send_message_fails_after_max_retries(
        self, notifier: DiscordNotifier
    ) -> None:
        notifier._channel.send.side_effect = discord.HTTPException(
            MagicMock(), "error"
        )
        result = await notifier.send_message("test")
        assert result is False
        assert notifier._channel.send.call_count == 3


class TestDiscordNotifierEmbeds:
    """Tests for Embed builder methods."""

    @pytest.fixture
    def notifier(self) -> DiscordNotifier:
        return DiscordNotifier()

    def test_build_opportunity_embed(self, notifier: DiscordNotifier) -> None:
        signal = FakeSignal()
        embed = notifier.build_opportunity_embed(signal)
        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOR_OPPORTUNITY
        assert "차익 기회 탐지" in embed.title
        field_names = [f.name for f in embed.fields]
        assert "심볼" in field_names
        assert "매수" in field_names
        assert "매도" in field_names

    def test_build_trade_result_embed_filled(self, notifier: DiscordNotifier) -> None:
        trade = FakeTrade(status="FILLED")
        embed = notifier.build_trade_result_embed(trade)
        assert embed.colour.value == COLOR_TRADE_SUCCESS
        assert "✅" in embed.title

    def test_build_trade_result_embed_partial(self, notifier: DiscordNotifier) -> None:
        trade = FakeTrade(status="PARTIAL")
        embed = notifier.build_trade_result_embed(trade)
        assert embed.colour.value == COLOR_TRADE_FAIL
        assert "⚠️" in embed.title

    def test_build_error_embed(self, notifier: DiscordNotifier) -> None:
        error = ValueError("bad price")
        embed = notifier.build_error_embed(error)
        assert embed.colour.value == COLOR_ERROR
        assert "에러 발생" in embed.title
        field_values = [f.value for f in embed.fields]
        assert any("ValueError" in v for v in field_values)


class TestDiscordNotifierFormat:
    """Tests for Notifier Protocol format_* methods (plain text)."""

    @pytest.fixture
    def notifier(self) -> DiscordNotifier:
        return DiscordNotifier()

    def test_format_opportunity(self, notifier: DiscordNotifier) -> None:
        msg = notifier.format_opportunity(FakeSignal())
        assert "차익 기회 탐지" in msg
        assert "BTC/USDT" in msg
        assert "binance" in msg
        assert "upbit" in msg

    def test_format_trade_result(self, notifier: DiscordNotifier) -> None:
        msg = notifier.format_trade_result(FakeTrade())
        assert "거래 체결 결과" in msg
        assert "FILLED" in msg
        assert "ETH/USDT" in msg

    def test_format_daily_summary(self, notifier: DiscordNotifier) -> None:
        msg = notifier.format_daily_summary(FakeDailySummary())
        assert "일일 PnL 요약" in msg
        assert "2026" in msg
        assert "142" in msg

    def test_format_daily_summary_negative(self, notifier: DiscordNotifier) -> None:
        stats = FakeDailySummary(net_pnl=-50.0)
        msg = notifier.format_daily_summary(stats)
        assert "📉" in msg

    def test_format_error(self, notifier: DiscordNotifier) -> None:
        msg = notifier.format_error(ValueError("oops"))
        assert "에러 발생" in msg
        assert "ValueError" in msg

    def test_format_system_status(self, notifier: DiscordNotifier) -> None:
        msg = notifier.format_system_status(FakeSystemStatus())
        assert "시스템 상태" in msg
        assert "paper" in msg
        assert "binance" in msg
