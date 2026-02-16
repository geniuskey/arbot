"""Interactive Telegram bot for ArBot status queries.

Provides command handlers (/status, /stats, /balance, /trades, /help)
so the user can query ArBot state directly from Telegram.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from arbot.logging import get_logger

if TYPE_CHECKING:
    from arbot.config import AppConfig
    from arbot.connectors.base import BaseConnector
    from arbot.core.simulator import PaperTradingSimulator
    from arbot.execution.paper_executor import PaperExecutor

logger = get_logger("telegram_bot")


class TelegramBotService:
    """Polling-based Telegram bot for interactive status queries.

    Args:
        bot_token: Telegram Bot API token.
        chat_id: Authorized chat ID (only this user can issue commands).
        simulator: Paper trading simulator instance.
        executor: Paper executor instance.
        connectors: List of exchange connectors.
        config: Application configuration.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        simulator: PaperTradingSimulator,
        executor: PaperExecutor,
        connectors: list[BaseConnector],
        config: AppConfig,
    ) -> None:
        self._chat_id = str(chat_id)
        self._simulator = simulator
        self._executor = executor
        self._connectors = connectors
        self._config = config
        self._started_at = datetime.now(UTC)

        self._app = (
            Application.builder()
            .token(bot_token)
            .build()
        )
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("stats", self._cmd_stats))
        self._app.add_handler(CommandHandler("balance", self._cmd_balance))
        self._app.add_handler(CommandHandler("trades", self._cmd_trades))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("start", self._cmd_help))

    def _is_authorized(self, update: Update) -> bool:
        return (
            update.effective_chat is not None
            and str(update.effective_chat.id) == self._chat_id
        )

    async def start(self) -> None:
        """Start the bot polling loop."""
        await self._app.initialize()
        await self._app.start()
        if self._app.updater is not None:
            await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram_bot_started")

    async def stop(self) -> None:
        """Stop the bot polling loop."""
        try:
            if self._app.updater is not None and self._app.updater.running:
                await self._app.updater.stop()
            if self._app.running:
                await self._app.stop()
            await self._app.shutdown()
        except Exception:
            pass
        logger.info("telegram_bot_stopped")

    async def _cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        uptime = datetime.now(UTC) - self._started_at
        hours = uptime.total_seconds() / 3600

        exchange_lines = []
        for c in self._connectors:
            exchange_lines.append(f"  {c.exchange_name}: {c.state.value}")

        mode = self._config.system.execution_mode.value
        symbols = len(self._config.symbols)
        running = "Running" if self._simulator.is_running else "Stopped"

        msg = (
            f"[ArBot Status]\n"
            f"State: {running}\n"
            f"Mode: {mode}\n"
            f"Uptime: {hours:.1f}h\n"
            f"Symbols: {symbols}\n"
            f"\nExchanges:\n" + "\n".join(exchange_lines)
        )
        assert update.message is not None
        await update.message.reply_text(msg)

    async def _cmd_stats(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        report = self._simulator.get_report()
        s = report.pipeline_stats

        msg = (
            f"[Pipeline Stats]\n"
            f"Cycles: {s.cycles_run:,}\n"
            f"Signals detected: {s.total_signals_detected}\n"
            f"Signals approved: {s.total_signals_approved}\n"
            f"Signals rejected: {s.total_signals_rejected}\n"
            f"Signals executed: {s.total_signals_executed}\n"
            f"Signals failed: {s.total_signals_failed}\n"
            f"\nPnL: ${s.total_pnl_usd:,.4f}\n"
            f"Fees: ${s.total_fees_usd:,.4f}\n"
            f"Net: ${s.total_pnl_usd - s.total_fees_usd:,.4f}\n"
            f"Trades: {report.trade_count}\n"
            f"Win rate: {report.win_rate:.1%}"
        )
        assert update.message is not None
        await update.message.reply_text(msg)

    async def _cmd_balance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        portfolio = self._executor.get_portfolio()
        lines = ["[Portfolio Balance]"]
        for ex_name, ex_bal in portfolio.exchange_balances.items():
            lines.append(f"\n{ex_name}:")
            for asset, bal in ex_bal.balances.items():
                if bal.total > 0:
                    lines.append(f"  {asset}: {bal.total:,.6f}")

        assert update.message is not None
        await update.message.reply_text("\n".join(lines))

    async def _cmd_trades(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        history = self._executor.trade_history[-5:]
        if not history:
            assert update.message is not None
            await update.message.reply_text("No trades yet.")
            return

        lines = [f"[Recent Trades ({len(history)})]"]
        for i, (buy, sell) in enumerate(reversed(history), 1):
            pnl = (
                sell.filled_quantity * sell.filled_price
                - buy.filled_quantity * buy.filled_price
            )
            lines.append(
                f"\n#{i} {buy.order.symbol}\n"
                f"  Buy: {buy.order.exchange} @ ${buy.filled_price:,.2f}\n"
                f"  Sell: {sell.order.exchange} @ ${sell.filled_price:,.2f}\n"
                f"  Qty: {buy.filled_quantity:.6f}\n"
                f"  PnL: ${pnl:,.4f}"
            )

        assert update.message is not None
        await update.message.reply_text("\n".join(lines))

    async def _cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        msg = (
            "[ArBot Commands]\n"
            "/status  - Bot status & exchanges\n"
            "/stats   - Pipeline statistics\n"
            "/balance - Portfolio balance\n"
            "/trades  - Recent trades (last 5)\n"
            "/help    - This message"
        )
        assert update.message is not None
        await update.message.reply_text(msg)
