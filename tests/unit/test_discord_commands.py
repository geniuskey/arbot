"""Unit tests for Discord slash commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from arbot.discord.views import ConfirmStopView, PaginatorView


# ---------------------------------------------------------------------------
# Fake data models matching the real interfaces
# ---------------------------------------------------------------------------


@dataclass
class FakePipelineStats:
    total_signals_detected: int = 10
    total_signals_approved: int = 8
    total_signals_rejected: int = 2
    total_signals_executed: int = 6
    total_signals_failed: int = 2
    total_pnl_usd: float = 150.0
    total_fees_usd: float = 20.0
    cycles_run: int = 1000
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FakeSignal:
    symbol: str = "BTC/USDT"
    buy_exchange: str = "binance"
    sell_exchange: str = "upbit"
    buy_price: float = 67000.0
    sell_price: float = 67500.0
    net_spread_pct: float = 0.5
    status: MagicMock = field(default_factory=lambda: MagicMock(value="EXECUTED"))
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FakeOrder:
    exchange: str = "binance"


@dataclass
class FakeTradeResult:
    order: FakeOrder = field(default_factory=FakeOrder)
    filled_quantity: float = 0.1
    filled_price: float = 67000.0
    fee: float = 0.5


@dataclass
class FakeSimulationReport:
    duration_seconds: float = 3600.0
    pipeline_stats: FakePipelineStats = field(default_factory=FakePipelineStats)
    final_pnl_usd: float = 150.0
    total_fees_usd: float = 20.0
    win_rate: float = 0.75
    trade_count: int = 6


@dataclass
class FakeAssetBalance:
    asset: str = "USDT"
    free: float = 10000.0
    locked: float = 0.0
    usd_value: float | None = 10000.0


@dataclass
class FakeExchangeBalance:
    exchange: str = "binance"
    balances: dict = field(default_factory=lambda: {
        "USDT": FakeAssetBalance(),
    })
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FakePortfolioSnapshot:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    exchange_balances: dict = field(default_factory=lambda: {
        "binance": FakeExchangeBalance(exchange="binance"),
        "upbit": FakeExchangeBalance(exchange="upbit"),
    })


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_ctx() -> MagicMock:
    """Create a mock BotContext with all components mocked."""
    ctx = MagicMock()

    # Config
    ctx.config.system.execution_mode.value = "paper"

    # Pipeline
    ctx.pipeline.get_stats.return_value = FakePipelineStats()
    ctx.pipeline.get_trade_log.return_value = [
        (FakeSignal(), FakeTradeResult(), FakeTradeResult(
            order=FakeOrder(exchange="upbit"),
            filled_price=67500.0,
        ))
    ]

    # Simulator
    ctx.simulator.is_running = True
    ctx.simulator.get_report.return_value = FakeSimulationReport()

    # Executor
    ctx.executor.get_portfolio.return_value = FakePortfolioSnapshot()
    ctx.executor.get_pnl.return_value = {"binance": {"USDT": 100.0}}
    ctx.executor._orderbooks = {}

    # Risk manager
    ctx.risk_manager.daily_pnl = -10.0
    ctx.risk_manager.consecutive_losses = 2
    ctx.risk_manager.is_in_cooldown = False
    ctx.risk_manager.trade_count = 6

    return ctx


@pytest.fixture
def mock_ctx() -> MagicMock:
    return _make_mock_ctx()


@pytest.fixture
def mock_interaction() -> MagicMock:
    """Create a mock Discord Interaction."""
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    # is_done() is a sync method in discord.py — use MagicMock, not AsyncMock
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# Info commands tests
# ---------------------------------------------------------------------------


class TestInfoCommands:
    """Tests for info slash command handler logic."""

    async def test_send_status_running(
        self, mock_ctx: MagicMock, mock_interaction: MagicMock
    ) -> None:
        from arbot.discord.cogs.info_commands import _send_status

        mock_ctx.simulator.is_running = True
        await _send_status(mock_interaction, mock_ctx)
        mock_interaction.response.send_message.assert_called_once()
        call_kwargs = mock_interaction.response.send_message.call_args
        embed = call_kwargs.kwargs.get("embed") or call_kwargs[1].get("embed")
        assert embed is not None
        assert "🟢" in embed.title

    async def test_send_status_stopped(
        self, mock_ctx: MagicMock, mock_interaction: MagicMock
    ) -> None:
        from arbot.discord.cogs.info_commands import _send_status

        mock_ctx.simulator.is_running = False
        await _send_status(mock_interaction, mock_ctx)
        call_kwargs = mock_interaction.response.send_message.call_args
        embed = call_kwargs.kwargs.get("embed") or call_kwargs[1].get("embed")
        assert "🔴" in embed.title

    async def test_send_status_cooldown(
        self, mock_ctx: MagicMock, mock_interaction: MagicMock
    ) -> None:
        from arbot.discord.cogs.info_commands import _send_status

        mock_ctx.risk_manager.is_in_cooldown = True
        await _send_status(mock_interaction, mock_ctx)
        call_kwargs = mock_interaction.response.send_message.call_args
        embed = call_kwargs.kwargs.get("embed") or call_kwargs[1].get("embed")
        risk_field = next(f for f in embed.fields if f.name == "리스크")
        assert "쿨다운" in risk_field.value


# ---------------------------------------------------------------------------
# Trading commands tests
# ---------------------------------------------------------------------------


class TestTradingCommands:
    """Tests for trading slash command logic."""

    async def test_confirm_stop_view_has_buttons(self) -> None:
        view = ConfirmStopView()
        assert len(view.children) == 2
        assert view.confirmed is None

    async def test_confirm_stop_view_confirm(self) -> None:
        view = ConfirmStopView()
        interaction = AsyncMock()
        interaction.response = AsyncMock()

        await view.confirm_btn.callback(interaction)
        assert view.confirmed is True

    async def test_confirm_stop_view_cancel(self) -> None:
        view = ConfirmStopView()
        interaction = AsyncMock()
        interaction.response = AsyncMock()

        await view.cancel_btn.callback(interaction)
        assert view.confirmed is False


# ---------------------------------------------------------------------------
# UI components tests
# ---------------------------------------------------------------------------


class TestPaginatorView:
    """Tests for PaginatorView."""

    async def test_single_page_buttons_disabled(self) -> None:
        pages = [discord.Embed(title="Page 1")]
        view = PaginatorView(pages)
        assert view._prev_btn.disabled is True
        assert view._next_btn.disabled is True

    async def test_multi_page_initial_state(self) -> None:
        pages = [discord.Embed(title=f"Page {i}") for i in range(3)]
        view = PaginatorView(pages)
        assert view._current == 0
        assert view._prev_btn.disabled is True
        assert view._next_btn.disabled is False

    async def test_next_button(self) -> None:
        pages = [discord.Embed(title=f"Page {i}") for i in range(3)]
        view = PaginatorView(pages)
        interaction = AsyncMock()
        interaction.response = AsyncMock()

        await view._next_btn.callback(interaction)
        assert view._current == 1
        assert view._prev_btn.disabled is False

    async def test_prev_button(self) -> None:
        pages = [discord.Embed(title=f"Page {i}") for i in range(3)]
        view = PaginatorView(pages)
        view._current = 2
        interaction = AsyncMock()
        interaction.response = AsyncMock()

        await view._prev_btn.callback(interaction)
        assert view._current == 1

    async def test_next_button_at_last_page(self) -> None:
        pages = [discord.Embed(title=f"Page {i}") for i in range(2)]
        view = PaginatorView(pages)
        view._current = 1
        interaction = AsyncMock()
        interaction.response = AsyncMock()

        await view._next_btn.callback(interaction)
        assert view._current == 1  # stays at last page

    async def test_current_embed(self) -> None:
        pages = [discord.Embed(title=f"Page {i}") for i in range(3)]
        view = PaginatorView(pages)
        assert view.current_embed.title == "Page 0"
        view._current = 2
        assert view.current_embed.title == "Page 2"


class TestMultiNotifierFanout:
    """Tests for AlertManager multi-notifier fan-out."""

    async def test_fanout_to_multiple_notifiers(self) -> None:
        from arbot.alerts.manager import AlertConfig, AlertManager

        notifier1 = AsyncMock()
        notifier1.send_message.return_value = True
        notifier2 = AsyncMock()
        notifier2.send_message.return_value = True

        config = AlertConfig(throttle_intervals={}, dedup_window_seconds=0.0)
        manager = AlertManager(notifier=[notifier1, notifier2], config=config)

        result = await manager.send_alert("test", "hello")
        assert result is True
        notifier1.send_message.assert_called_once_with("hello")
        notifier2.send_message.assert_called_once_with("hello")

    async def test_fanout_partial_failure(self) -> None:
        from arbot.alerts.manager import AlertConfig, AlertManager

        notifier1 = AsyncMock()
        notifier1.send_message.return_value = False
        notifier2 = AsyncMock()
        notifier2.send_message.return_value = True

        config = AlertConfig(throttle_intervals={}, dedup_window_seconds=0.0)
        manager = AlertManager(notifier=[notifier1, notifier2], config=config)

        result = await manager.send_alert("test", "hello")
        assert result is True  # at least one succeeded

    async def test_fanout_all_fail(self) -> None:
        from arbot.alerts.manager import AlertConfig, AlertManager

        notifier1 = AsyncMock()
        notifier1.send_message.return_value = False
        notifier2 = AsyncMock()
        notifier2.send_message.return_value = False

        config = AlertConfig(throttle_intervals={}, dedup_window_seconds=0.0)
        manager = AlertManager(notifier=[notifier1, notifier2], config=config)

        result = await manager.send_alert("test", "hello")
        assert result is False

    async def test_fanout_exception_doesnt_block_others(self) -> None:
        from arbot.alerts.manager import AlertConfig, AlertManager

        notifier1 = AsyncMock()
        notifier1.send_message.side_effect = RuntimeError("boom")
        notifier2 = AsyncMock()
        notifier2.send_message.return_value = True

        config = AlertConfig(throttle_intervals={}, dedup_window_seconds=0.0)
        manager = AlertManager(notifier=[notifier1, notifier2], config=config)

        result = await manager.send_alert("test", "hello")
        assert result is True  # notifier2 succeeded despite notifier1 crash
