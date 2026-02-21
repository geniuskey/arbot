"""Interactive Telegram bot for ArBot status queries.

Provides command handlers (/status, /stats, /balance, /trades, /debug, /help)
so the user can query ArBot state directly from Telegram.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from arbot.logging import get_logger

if TYPE_CHECKING:
    from arbot.config import AppConfig
    from arbot.connectors.base import BaseConnector
    from arbot.core.collector import PriceCollector
    from arbot.core.simulator import PaperTradingSimulator
    from arbot.execution.paper_executor import PaperExecutor
    from arbot.funding.manager import FundingRateManager
    from arbot.storage.redis_cache import RedisCache

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
        redis_cache: Redis cache for orderbook queries.
        collector: Price collector for stats.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        simulator: PaperTradingSimulator,
        executor: PaperExecutor,
        connectors: list[BaseConnector],
        config: AppConfig,
        redis_cache: RedisCache | None = None,
        collector: PriceCollector | None = None,
        funding_manager: FundingRateManager | None = None,
    ) -> None:
        self._chat_id = str(chat_id)
        self._simulator = simulator
        self._executor = executor
        self._connectors = connectors
        self._config = config
        self._redis_cache = redis_cache
        self._collector = collector
        self._funding_manager = funding_manager
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
        self._app.add_handler(CommandHandler("debug", self._cmd_debug))
        self._app.add_handler(CommandHandler("funding", self._cmd_funding))
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

        if s.rejection_reasons:
            msg += "\n\n[Rejection Reasons]"
            for reason, count in sorted(
                s.rejection_reasons.items(), key=lambda x: x[1], reverse=True
            ):
                short = reason[:50]
                msg += f"\n  {short}: {count}"

        if self._funding_manager is not None:
            fs = self._funding_manager.get_stats()
            open_count = len(self._funding_manager.open_positions)
            msg += (
                f"\n\n[Funding Rate]\n"
                f"Open: {open_count} / Closed: {fs.total_positions_closed}\n"
                f"Collected: ${fs.total_funding_collected:,.4f}\n"
                f"Fees: ${fs.total_fees_paid:,.4f}\n"
                f"Net PnL: ${fs.total_net_pnl:,.4f}\n"
                f"Rate checks: {fs.rate_checks}"
            )

        assert update.message is not None
        await update.message.reply_text(msg)

    async def _cmd_balance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        # Build mid-price map from orderbooks for USDT valuation
        mid_prices: dict[str, float] = {"USDT": 1.0}
        for key, ob in self._executor.orderbooks.items():
            base = ob.symbol.split("/")[0]
            if base in mid_prices:
                continue
            if ob.bids and ob.asks:
                mid_prices[base] = (ob.bids[0].price + ob.asks[0].price) / 2
            elif ob.bids:
                mid_prices[base] = ob.bids[0].price
            elif ob.asks:
                mid_prices[base] = ob.asks[0].price

        portfolio = self._executor.get_portfolio()
        lines = ["[Portfolio Balance]"]
        grand_total = 0.0
        for ex_name, ex_bal in portfolio.exchange_balances.items():
            ex_total = 0.0
            asset_lines: list[str] = []
            for asset, bal in ex_bal.balances.items():
                if bal.total > 0:
                    price = mid_prices.get(asset, 0.0)
                    usd_val = bal.total * price
                    ex_total += usd_val
                    if price > 0 and asset != "USDT":
                        asset_lines.append(
                            f"  {asset}: {bal.total:,.6f} (${usd_val:,.2f})"
                        )
                    else:
                        asset_lines.append(f"  {asset}: ${bal.total:,.2f}")
            lines.append(f"\n{ex_name}: ${ex_total:,.2f}")
            lines.extend(asset_lines)

        grand_total = sum(
            bal.total * mid_prices.get(asset, 0.0)
            for ex_bal in portfolio.exchange_balances.values()
            for asset, bal in ex_bal.balances.items()
            if bal.total > 0
        )
        lines.append(f"\nTotal: ${grand_total:,.2f}")

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

    async def _cmd_debug(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        lines = ["[Debug Info]"]

        # Collector stats
        if self._collector is not None:
            status = self._collector.get_status()
            lines.append("\n-- Collector --")
            for ex_name, ex_status in status.get("exchanges", {}).items():
                lines.append(
                    f"  {ex_name}: "
                    f"connected={ex_status['connected']} "
                    f"ob={ex_status['orderbook_count']} "
                    f"trades={ex_status['trade_count']}"
                )

        # Redis orderbook check
        if self._redis_cache is not None:
            lines.append("\n-- Redis Orderbooks --")
            for symbol in self._config.symbols:
                try:
                    obs = await self._redis_cache.get_all_orderbooks(symbol)
                    if not obs:
                        lines.append(f"  {symbol}: (empty)")
                        continue
                    ex_names = list(obs.keys())
                    lines.append(f"  {symbol}: {len(obs)} exchanges {ex_names}")

                    # Show best bid/ask per exchange and spread between them
                    prices = []
                    for ex, ob in obs.items():
                        best_ask = ob.asks[0].price if ob.asks else 0
                        best_bid = ob.bids[0].price if ob.bids else 0
                        lines.append(
                            f"    {ex}: bid={best_bid:,.2f} ask={best_ask:,.2f}"
                        )
                        if best_bid > 0 and best_ask > 0:
                            prices.append((ex, best_bid, best_ask))

                    # Cross-exchange spread
                    if len(prices) >= 2:
                        for i in range(len(prices)):
                            for j in range(len(prices)):
                                if i == j:
                                    continue
                                buy_ex, _, buy_ask = prices[i]
                                sell_ex, sell_bid, _ = prices[j]
                                if buy_ask > 0:
                                    spread = (sell_bid - buy_ask) / buy_ask * 100
                                    lines.append(
                                        f"    {buy_ex}->{sell_ex}: "
                                        f"spread={spread:+.4f}%"
                                    )
                except Exception as e:
                    lines.append(f"  {symbol}: error={e}")

        # Config thresholds
        lines.append("\n-- Thresholds --")
        lines.append(f"  min_spread_pct: {self._config.detector.spatial.min_spread_pct}%")
        lines.append(f"  min_depth_usd: ${self._config.detector.spatial.min_depth_usd}")
        lines.append(f"  min_net_spread_pct: {self._config.risk.min_net_spread_pct}%")

        # Exchange fees
        lines.append("\n-- Fees (VIP) --")
        for ex_name in self._config.exchanges_enabled:
            from arbot.config import ExchangeConfig
            ex_cfg = self._config.exchange_configs.get(ex_name, ExchangeConfig())
            lines.append(
                f"  {ex_name}: maker={ex_cfg.maker_fee_pct:.3f}% "
                f"taker={ex_cfg.taker_fee_pct:.3f}%"
            )

        # Funding rates
        if self._funding_manager is not None and self._funding_manager.latest_rates:
            lines.append("\n-- Funding Rates --")
            for key, snap in sorted(self._funding_manager.latest_rates.items()):
                lines.append(
                    f"  {snap.exchange} {snap.symbol}: "
                    f"{snap.funding_rate:.6f} "
                    f"({snap.annualized_rate:.1f}%/yr)"
                )

        assert update.message is not None
        text = "\n".join(lines)
        if len(text) > 4096:
            text = text[:4093] + "..."
        await update.message.reply_text(text)

    async def _cmd_funding(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        assert update.message is not None

        if self._funding_manager is None:
            await update.message.reply_text("Funding rate arb is disabled.")
            return

        stats = self._funding_manager.get_stats()
        open_positions = self._funding_manager.open_positions
        latest = self._funding_manager.latest_rates

        lines = ["[Funding Rate Arbitrage]"]
        lines.append(
            f"Status: {'Running' if self._funding_manager.is_running else 'Stopped'}"
        )
        lines.append(f"Rate checks: {stats.rate_checks}")
        lines.append(f"Settlements: {stats.funding_settlements}")
        lines.append(f"Positions opened: {stats.total_positions_opened}")
        lines.append(f"Positions closed: {stats.total_positions_closed}")
        lines.append(f"Funding collected: ${stats.total_funding_collected:,.4f}")
        lines.append(f"Fees paid: ${stats.total_fees_paid:,.4f}")
        lines.append(f"Net PnL: ${stats.total_net_pnl:,.4f}")

        if open_positions:
            lines.append(f"\n-- Open Positions ({len(open_positions)}) --")
            for pos in open_positions:
                lines.append(
                    f"  {pos.exchange} {pos.symbol}\n"
                    f"    qty={pos.quantity:.6f} "
                    f"funding=${pos.total_funding_collected:,.4f}\n"
                    f"    payments={pos.funding_payments} "
                    f"hours={pos.holding_hours:.1f}"
                )

        if latest:
            lines.append(f"\n-- Latest Rates ({len(latest)}) --")
            for key, snap in sorted(latest.items()):
                price_str = ""
                if snap.index_price > 0:
                    price_str = f" idx=${snap.index_price:,.0f}"
                lines.append(
                    f"  {snap.exchange} {snap.symbol.split(':')[0]}: "
                    f"{snap.funding_rate:.6f} "
                    f"({snap.annualized_rate:.1f}%/yr)"
                    f"{price_str}"
                )

        text = "\n".join(lines)
        if len(text) > 4096:
            text = text[:4093] + "..."
        await update.message.reply_text(text)

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
            "/funding - Funding rate arb status\n"
            "/debug   - Orderbook & collector debug\n"
            "/help    - This message"
        )
        assert update.message is not None
        await update.message.reply_text(msg)
