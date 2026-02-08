"""Unit tests for alerts module (TelegramNotifier + AlertManager)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import telegram.error

from arbot.alerts.manager import AlertConfig, AlertManager, AlertPriority
from arbot.alerts.telegram import TelegramNotifier, _escape_md


# ---------------------------------------------------------------------------
# Test data fixtures
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
# TelegramNotifier tests
# ---------------------------------------------------------------------------


class TestEscapeMarkdown:
    """Tests for MarkdownV2 escaping utility."""

    def test_escapes_special_chars(self) -> None:
        assert _escape_md("hello.world!") == r"hello\.world\!"

    def test_preserves_normal_text(self) -> None:
        assert _escape_md("hello world") == "hello world"

    def test_escapes_all_special(self) -> None:
        for char in r"_*[]()~`>#+-=|{}.!":
            assert _escape_md(char) == f"\\{char}"

    def test_numeric_input(self) -> None:
        assert _escape_md("12.34%") == r"12\.34%"


class TestTelegramNotifier:
    """Tests for TelegramNotifier message sending and formatting."""

    @pytest.fixture
    def notifier(self) -> TelegramNotifier:
        return TelegramNotifier(bot_token="fake-token", chat_id="12345")

    @pytest.fixture
    def mock_bot(self, notifier: TelegramNotifier) -> AsyncMock:
        mock = AsyncMock()
        notifier._bot = mock
        return mock

    async def test_send_message_success(
        self, notifier: TelegramNotifier, mock_bot: AsyncMock
    ) -> None:
        mock_bot.send_message.return_value = MagicMock()
        result = await notifier.send_message("test message")
        assert result is True
        mock_bot.send_message.assert_called_once_with(
            chat_id="12345",
            text="test message",
            parse_mode="MarkdownV2",
        )

    async def test_send_message_custom_parse_mode(
        self, notifier: TelegramNotifier, mock_bot: AsyncMock
    ) -> None:
        mock_bot.send_message.return_value = MagicMock()
        await notifier.send_message("test", parse_mode="HTML")
        mock_bot.send_message.assert_called_once_with(
            chat_id="12345",
            text="test",
            parse_mode="HTML",
        )

    async def test_send_message_retries_on_failure(
        self, notifier: TelegramNotifier, mock_bot: AsyncMock
    ) -> None:
        mock_bot.send_message.side_effect = [
            telegram.error.TelegramError("network error"),
            telegram.error.TelegramError("network error"),
            MagicMock(),
        ]
        result = await notifier.send_message("test")
        assert result is True
        assert mock_bot.send_message.call_count == 3

    async def test_send_message_fails_after_max_retries(
        self, notifier: TelegramNotifier, mock_bot: AsyncMock
    ) -> None:
        mock_bot.send_message.side_effect = telegram.error.TelegramError("fail")
        result = await notifier.send_message("test")
        assert result is False
        assert mock_bot.send_message.call_count == 3

    async def test_send_message_retry_after(
        self, notifier: TelegramNotifier, mock_bot: AsyncMock
    ) -> None:
        mock_bot.send_message.side_effect = [
            telegram.error.RetryAfter(0.01),
            MagicMock(),
        ]
        result = await notifier.send_message("test")
        assert result is True
        assert mock_bot.send_message.call_count == 2

    def test_format_opportunity(self, notifier: TelegramNotifier) -> None:
        signal = FakeSignal()
        msg = notifier.format_opportunity(signal)
        assert "차익 기회 탐지" in msg
        assert "BTC/USDT" in msg
        assert "binance" in msg
        assert "upbit" in msg
        assert r"0\.547" in msg

    def test_format_trade_result(self, notifier: TelegramNotifier) -> None:
        trade = FakeTrade()
        msg = notifier.format_trade_result(trade)
        assert "거래 체결 결과" in msg
        assert "FILLED" in msg
        assert "ETH/USDT" in msg
        assert "binance" in msg

    def test_format_trade_result_non_filled(self, notifier: TelegramNotifier) -> None:
        trade = FakeTrade(status="PARTIAL")
        msg = notifier.format_trade_result(trade)
        assert "PARTIAL" in msg

    def test_format_daily_summary(self, notifier: TelegramNotifier) -> None:
        stats = FakeDailySummary()
        msg = notifier.format_daily_summary(stats)
        assert "일일 PnL 요약" in msg
        assert "2026" in msg
        assert "142" in msg
        assert "38" in msg

    def test_format_daily_summary_negative_pnl(
        self, notifier: TelegramNotifier
    ) -> None:
        stats = FakeDailySummary(net_pnl=-50.0, total_pnl=-30.0)
        msg = notifier.format_daily_summary(stats)
        assert "일일 PnL 요약" in msg

    def test_format_error(self, notifier: TelegramNotifier) -> None:
        error = ValueError("Invalid price: -1.0")
        msg = notifier.format_error(error)
        assert "에러 발생" in msg
        assert "ValueError" in msg
        assert "Invalid price" in msg

    def test_format_system_status(self, notifier: TelegramNotifier) -> None:
        status = FakeSystemStatus()
        msg = notifier.format_system_status(status)
        assert "시스템 상태" in msg
        assert "paper" in msg
        assert "binance" in msg
        assert r"12\.5" in msg


# ---------------------------------------------------------------------------
# AlertManager tests
# ---------------------------------------------------------------------------


class TestAlertManager:
    """Tests for AlertManager throttling, dedup, and priority."""

    @pytest.fixture
    def mock_notifier(self) -> AsyncMock:
        notifier = AsyncMock(spec=TelegramNotifier)
        notifier.send_message.return_value = True
        return notifier

    @pytest.fixture
    def manager(self, mock_notifier: AsyncMock) -> AlertManager:
        config = AlertConfig(
            throttle_intervals={"opportunity": 1.0, "error": 0.5},
            dedup_window_seconds=2.0,
            max_history=10,
        )
        return AlertManager(notifier=mock_notifier, config=config)

    async def test_send_alert_success(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        result = await manager.send_alert("opportunity", "test msg")
        assert result is True
        mock_notifier.send_message.assert_called_once_with("test msg")

    async def test_throttling_blocks_rapid_alerts(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        await manager.send_alert("opportunity", "first")
        result = await manager.send_alert("opportunity", "second")
        assert result is False
        assert mock_notifier.send_message.call_count == 1

    async def test_throttling_allows_after_interval(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        config = AlertConfig(
            throttle_intervals={"opportunity": 0.01},
            dedup_window_seconds=0.0,
        )
        mgr = AlertManager(notifier=mock_notifier, config=config)
        await mgr.send_alert("opportunity", "first")
        await asyncio.sleep(0.02)
        result = await mgr.send_alert("opportunity", "second")
        assert result is True
        assert mock_notifier.send_message.call_count == 2

    async def test_different_types_not_throttled(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        await manager.send_alert("opportunity", "opp msg")
        result = await manager.send_alert("error", "err msg")
        assert result is True
        assert mock_notifier.send_message.call_count == 2

    async def test_dedup_blocks_identical_messages(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        manager.clear_throttle()
        config = AlertConfig(
            throttle_intervals={},
            dedup_window_seconds=10.0,
        )
        mgr = AlertManager(notifier=mock_notifier, config=config)
        await mgr.send_alert("error", "same message")
        result = await mgr.send_alert("error", "same message")
        assert result is False

    async def test_dedup_allows_different_messages(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        config = AlertConfig(
            throttle_intervals={},
            dedup_window_seconds=10.0,
        )
        mgr = AlertManager(notifier=mock_notifier, config=config)
        await mgr.send_alert("error", "message A")
        result = await mgr.send_alert("error", "message B")
        assert result is True

    async def test_critical_bypasses_throttle(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        await manager.send_alert("opportunity", "first", AlertPriority.MEDIUM)
        result = await manager.send_alert(
            "opportunity", "critical!", AlertPriority.CRITICAL
        )
        assert result is True
        assert mock_notifier.send_message.call_count == 2

    async def test_critical_does_not_bypass_when_disabled(
        self, mock_notifier: AsyncMock
    ) -> None:
        config = AlertConfig(
            throttle_intervals={"opportunity": 100.0},
            critical_bypass_throttle=False,
        )
        mgr = AlertManager(notifier=mock_notifier, config=config)
        await mgr.send_alert("opportunity", "first")
        result = await mgr.send_alert(
            "opportunity", "critical!", AlertPriority.CRITICAL
        )
        assert result is False

    async def test_history_tracking(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        await manager.send_alert("error", "msg1")
        await manager.send_alert("trade_result", "msg2")
        history = manager.history
        assert len(history) == 2
        assert history[0].alert_type == "error"
        assert history[1].alert_type == "trade_result"
        assert all(r.delivered for r in history)

    async def test_history_max_size(self, mock_notifier: AsyncMock) -> None:
        config = AlertConfig(
            throttle_intervals={},
            dedup_window_seconds=0.0,
            max_history=3,
        )
        mgr = AlertManager(notifier=mock_notifier, config=config)
        for i in range(5):
            await mgr.send_alert("error", f"msg {i}")
        assert len(mgr.history) == 3

    async def test_delivery_failure_recorded(
        self, mock_notifier: AsyncMock
    ) -> None:
        mock_notifier.send_message.return_value = False
        config = AlertConfig(throttle_intervals={}, dedup_window_seconds=0.0)
        mgr = AlertManager(notifier=mock_notifier, config=config)
        result = await mgr.send_alert("error", "fail msg")
        assert result is False
        assert len(mgr.history) == 1
        assert mgr.history[0].delivered is False

    async def test_clear_throttle_specific(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        await manager.send_alert("opportunity", "first")
        manager.clear_throttle("opportunity")
        result = await manager.send_alert("opportunity", "second after clear")
        assert result is True

    async def test_clear_throttle_all(
        self, manager: AlertManager, mock_notifier: AsyncMock
    ) -> None:
        await manager.send_alert("opportunity", "opp")
        await manager.send_alert("error", "err")
        manager.clear_throttle()
        manager.clear_dedup()
        r1 = await manager.send_alert("opportunity", "opp2")
        r2 = await manager.send_alert("error", "err2")
        assert r1 is True
        assert r2 is True

    async def test_unthrottled_type_always_sends(
        self, mock_notifier: AsyncMock
    ) -> None:
        config = AlertConfig(
            throttle_intervals={"opportunity": 100.0},
            dedup_window_seconds=0.0,
        )
        mgr = AlertManager(notifier=mock_notifier, config=config)
        await mgr.send_alert("custom_type", "msg1")
        result = await mgr.send_alert("custom_type", "msg2")
        assert result is True
