"""ArBot entry point.

Assembles all system components (connectors, cache, detectors, executor,
risk manager, pipeline) from configuration and runs the arbitrage system
with graceful shutdown support.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from arbot.alerts.manager import AlertManager
from arbot.alerts.notifier_protocol import Notifier
from arbot.config import AppConfig, ExecutionMode, ExchangeConfig, load_config
from arbot.connectors.base import BaseConnector
from arbot.connectors.binance import BinanceConnector
from arbot.connectors.bybit import BybitConnector
from arbot.connectors.kucoin import KuCoinConnector
from arbot.connectors.okx import OKXConnector
from arbot.connectors.upbit import UpbitConnector
from arbot.core.collector import PriceCollector
from arbot.core.pipeline import ArbitragePipeline
from arbot.core.simulator import PaperTradingSimulator
from arbot.detector.spatial import SpatialDetector
from arbot.detector.triangular import TriangularDetector
from arbot.execution.paper_executor import PaperExecutor
from arbot.logging import get_logger, setup_logging
from arbot.models.config import ExchangeInfo, RiskConfig, TradingFee
from arbot.models.trade import TradeResult
from arbot.risk.manager import RiskManager
from arbot.storage.redis_cache import RedisCache

# Discord integration (optional)
try:
    from arbot.alerts.discord_notifier import DiscordNotifier
    from arbot.discord.bot import ArBotDiscord
    from arbot.discord.context import BotContext

    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False


# Mapping of exchange names to connector classes
_CONNECTOR_CLASSES: dict[str, type[BaseConnector]] = {
    "binance": BinanceConnector,
    "bybit": BybitConnector,
    "kucoin": KuCoinConnector,
    "okx": OKXConnector,
    "upbit": UpbitConnector,
}


def _build_exchange_info(name: str, exchange_config: ExchangeConfig) -> ExchangeInfo:
    """Build an ExchangeInfo model from exchange configuration.

    Args:
        name: Exchange identifier.
        exchange_config: Exchange-specific configuration.

    Returns:
        ExchangeInfo instance.
    """
    return ExchangeInfo(
        name=name,
        tier=exchange_config.tier,
        is_active=True,
        fees=TradingFee(
            maker_pct=exchange_config.maker_fee_pct,
            taker_pct=exchange_config.taker_fee_pct,
        ),
        rate_limit=exchange_config.rate_limit.model_dump(),
    )


def _create_connectors(config: AppConfig) -> list[BaseConnector]:
    """Create exchange connectors for all enabled exchanges.

    Only creates connectors for exchanges that have a registered
    connector class. Others are logged as warnings and skipped.

    Args:
        config: Application configuration.

    Returns:
        List of initialized exchange connectors.
    """
    logger = get_logger("main")
    connectors: list[BaseConnector] = []

    for exchange_name in config.exchanges_enabled:
        connector_cls = _CONNECTOR_CLASSES.get(exchange_name)
        if connector_cls is None:
            logger.warning(
                "no_connector_implementation",
                exchange=exchange_name,
                msg="Skipping exchange: no connector class registered",
            )
            continue

        exchange_config = config.exchange_configs.get(
            exchange_name, ExchangeConfig()
        )
        info = _build_exchange_info(exchange_name, exchange_config)

        # Read API keys from environment variables
        api_key = os.environ.get(f"ARBOT_{exchange_name.upper()}_API_KEY", "")
        api_secret = os.environ.get(f"ARBOT_{exchange_name.upper()}_API_SECRET", "")

        extra_kwargs: dict[str, str] = {}
        passphrase = os.environ.get(f"ARBOT_{exchange_name.upper()}_PASSPHRASE", "")
        if passphrase:
            extra_kwargs["passphrase"] = passphrase

        connector = connector_cls(
            config=info,
            api_key=api_key,
            api_secret=api_secret,
            **extra_kwargs,
        )
        connectors.append(connector)
        logger.info(
            "connector_created",
            exchange=exchange_name,
            tier=exchange_config.tier,
        )

    return connectors


def _build_exchange_fees(config: AppConfig) -> dict[str, TradingFee]:
    """Build fee schedule mapping from configuration.

    Args:
        config: Application configuration.

    Returns:
        Mapping of exchange name to TradingFee.
    """
    fees: dict[str, TradingFee] = {}
    for name in config.exchanges_enabled:
        ex_config = config.exchange_configs.get(name, ExchangeConfig())
        fees[name] = TradingFee(
            maker_pct=ex_config.maker_fee_pct,
            taker_pct=ex_config.taker_fee_pct,
        )
    return fees


def _build_initial_balances(config: AppConfig) -> dict[str, dict[str, float]]:
    """Build initial paper trading balances for all enabled exchanges.

    Args:
        config: Application configuration.

    Returns:
        Mapping of exchange name to {asset: amount}.
    """
    balances: dict[str, dict[str, float]] = {}
    for name in config.exchanges_enabled:
        balances[name] = {
            "USDT": 1_000.0,
            "BTC": 0.01,
            "ETH": 0.2,
        }
    return balances


async def run(
    config: AppConfig,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Run the ArBot system.

    Assembles all components, starts price collection, and runs the
    simulation pipeline until interrupted.

    Args:
        config: Validated application configuration.
        shutdown_event: Optional external event to trigger shutdown.
            If not provided, one is created internally with signal handlers.
    """
    logger = get_logger("main")
    logger.info("arbot_starting", mode=config.system.execution_mode.value)

    # Create exchange connectors
    connectors = _create_connectors(config)
    if not connectors:
        logger.error("no_connectors", msg="No exchange connectors available. Exiting.")
        return

    # Create Redis cache
    redis_url = config.database.redis.url
    redis_cache = RedisCache(redis_url=redis_url)

    # Collect all required symbols (base + triangular intermediate pairs)
    all_symbols_set: set[str] = set(config.symbols)
    if config.detector.triangular.enabled:
        for path in config.detector.triangular.paths:
            all_symbols_set.update(path)
    all_symbols = sorted(all_symbols_set)

    # Create price collector
    collector = PriceCollector(
        connectors=connectors,
        redis_cache=redis_cache,
        symbols=all_symbols,
    )

    # Build exchange fees
    exchange_fees = _build_exchange_fees(config)

    # Create detectors
    spatial_detector = None
    if config.detector.spatial.enabled:
        spatial_detector = SpatialDetector(
            min_spread_pct=config.detector.spatial.min_spread_pct,
            min_depth_usd=config.detector.spatial.min_depth_usd,
            exchange_fees=exchange_fees,
            default_quantity_usd=500.0,
        )
        logger.info("spatial_detector_enabled")

    # Create triangular detector
    triangular_detector = None
    if config.detector.triangular.enabled:
        # Use fees from the first available exchange as default
        first_fee = next(iter(exchange_fees.values()), TradingFee(maker_pct=0.1, taker_pct=0.1))
        triangular_detector = TriangularDetector(
            min_profit_pct=config.detector.triangular.min_profit_pct,
            default_fee=first_fee,
        )
        logger.info("triangular_detector_enabled")

    # Create risk manager
    risk_config = RiskConfig(
        max_position_per_coin_usd=config.risk.max_position_per_coin_usd,
        max_total_exposure_usd=config.risk.max_total_exposure_usd,
        max_daily_loss_usd=config.risk.max_daily_loss_usd,
        price_deviation_threshold_pct=config.risk.price_deviation_threshold_pct,
        max_spread_pct=config.risk.max_spread_pct,
        consecutive_loss_limit=config.risk.consecutive_loss_limit,
        cooldown_minutes=config.risk.cooldown_minutes,
    )
    risk_manager = RiskManager(config=risk_config)

    # Create executor
    initial_balances = _build_initial_balances(config)
    executor = PaperExecutor(
        initial_balances=initial_balances,
        exchange_fees=exchange_fees,
    )

    # Assemble pipeline
    pipeline = ArbitragePipeline(
        executor=executor,
        risk_manager=risk_manager,
        spatial_detector=spatial_detector,
        triangular_detector=triangular_detector,
    )

    # Create simulator with orderbook provider from Redis
    async def orderbook_provider() -> list[dict]:
        """Fetch latest orderbooks from Redis, one dict per symbol.

        Returns a list of dicts, each mapping exchange name to OrderBook
        for a single symbol. Only includes symbols with 2+ exchange data.
        """
        result: list[dict] = []
        for symbol in config.symbols:
            obs = await redis_cache.get_all_orderbooks(symbol)
            if len(obs) >= 2:
                result.append(obs)
        return result

    # Triangular orderbook provider: per-exchange, multi-symbol
    triangular_provider = None
    if config.detector.triangular.enabled:
        async def _triangular_provider() -> dict[str, dict]:
            """Fetch orderbooks per exchange for triangular detection."""
            result: dict[str, dict] = {}
            for c in connectors:
                exchange_obs = {}
                for symbol in all_symbols:
                    ob = await redis_cache.get_orderbook(c.exchange_name, symbol)
                    if ob is not None:
                        exchange_obs[symbol] = ob
                if len(exchange_obs) >= 3:
                    result[c.exchange_name] = exchange_obs
            return result

        triangular_provider = _triangular_provider

    # Setup notification channels
    notifiers: list[Notifier] = []
    discord_bot: ArBotDiscord | None = None
    discord_task: asyncio.Task[None] | None = None

    # Telegram notifier (if configured)
    telegram_notifier: TelegramNotifier | None = None
    if config.alerts.telegram.enabled and config.alerts.telegram.bot_token:
        from arbot.alerts.telegram import TelegramNotifier

        telegram_notifier = TelegramNotifier(
            bot_token=config.alerts.telegram.bot_token,
            chat_id=config.alerts.telegram.chat_id,
        )
        notifiers.append(telegram_notifier)
        logger.info("telegram_notifier_enabled")

    # Alert manager
    alert_manager: AlertManager | None = None
    if notifiers:
        alert_manager = AlertManager(notifier=notifiers)

    # Trade callback for notifications
    async def _on_trade(
        buy: TradeResult, sell: TradeResult, pnl: float
    ) -> None:
        if alert_manager is None or telegram_notifier is None:
            return
        msg = (
            f"*Trade Executed*\n\n"
            f"Buy: {buy.exchange} @ ${buy.filled_price:,.2f}\n"
            f"Sell: {sell.exchange} @ ${sell.filled_price:,.2f}\n"
            f"Qty: {buy.filled_quantity:.6f}\n"
            f"PnL: ${pnl:,.4f}\n"
            f"Fees: ${buy.fee + sell.fee:,.4f}"
        )
        await alert_manager.send_alert("trade_result", msg)

    simulator = PaperTradingSimulator(
        pipeline=pipeline, interval_seconds=1.0, on_trade=_on_trade,
    )

    # Create funding rate manager (if enabled)
    funding_manager = None
    if config.detector.funding.enabled:
        from arbot.detector.funding import FundingRateDetector
        from arbot.funding.manager import FundingRateManager

        funding_detector = FundingRateDetector(
            min_rate_threshold=config.detector.funding.min_rate_threshold,
            min_annualized_pct=config.detector.funding.min_annualized_pct,
            symbols=config.detector.funding.perp_symbols,
        )
        funding_manager = FundingRateManager(
            detector=funding_detector,
            executor=executor,
            risk_manager=risk_manager,
            connectors=connectors,
            max_positions=config.detector.funding.max_positions,
            position_size_usd=config.detector.funding.position_size_usd,
            close_threshold=config.detector.funding.close_threshold_pct,
            check_interval_seconds=config.detector.funding.check_interval_seconds,
        )
        logger.info("funding_rate_manager_enabled")

    # Telegram interactive bot (if configured)
    telegram_bot_service: TelegramBotService | None = None
    if config.alerts.telegram.enabled and config.alerts.telegram.bot_token:
        from arbot.alerts.telegram_bot import TelegramBotService

        telegram_bot_service = TelegramBotService(
            bot_token=config.alerts.telegram.bot_token,
            chat_id=config.alerts.telegram.chat_id,
            simulator=simulator,
            executor=executor,
            connectors=connectors,
            config=config,
            redis_cache=redis_cache,
            collector=collector,
            funding_manager=funding_manager,
        )

    # Discord bot (if configured)
    discord_notifier: DiscordNotifier | None = None
    if HAS_DISCORD and config.alerts.discord.enabled and config.alerts.discord.bot_token:
        discord_notifier = DiscordNotifier()
        notifiers.append(discord_notifier)

        bot_context = BotContext(
            config=config,
            pipeline=pipeline,
            simulator=simulator,
            executor=executor,
            risk_manager=risk_manager,
        )
        discord_bot = ArBotDiscord(
            bot_context=bot_context,
            discord_notifier=discord_notifier,
            guild_id=config.alerts.discord.guild_id,
            channel_id=config.alerts.discord.channel_id,
        )
        logger.info("discord_bot_enabled")

    # Setup shutdown event
    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown_signal_received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except NotImplementedError:
        # Windows does not support loop.add_signal_handler;
        # fall back to signal.signal() which works cross-platform.
        def _sync_signal_handler(signum: int, frame: object) -> None:
            loop.call_soon_threadsafe(_signal_handler)

        signal.signal(signal.SIGINT, _sync_signal_handler)
        signal.signal(signal.SIGTERM, _sync_signal_handler)

    try:
        # Connect Redis
        await redis_cache.connect()
        logger.info("redis_connected")

        # Start price collection
        await collector.start()
        logger.info("price_collector_started")

        # Start simulator
        await simulator.start(
            orderbook_provider=orderbook_provider,
            triangular_provider=triangular_provider,
        )
        logger.info(
            "simulator_started",
            mode=config.system.execution_mode.value,
            symbols=config.symbols,
            exchanges=[c.exchange_name for c in connectors],
        )

        # Send startup notification
        if alert_manager is not None:
            exchanges_str = ", ".join(c.exchange_name for c in connectors)
            await alert_manager.send_alert(
                "system_status",
                f"ArBot started\n"
                f"Mode: {config.system.execution_mode.value}\n"
                f"Exchanges: {exchanges_str}\n"
                f"Symbols: {len(config.symbols)}",
            )

        # Start funding rate manager
        if funding_manager is not None:
            await funding_manager.start()
            logger.info("funding_rate_manager_started")

        # Start Telegram interactive bot
        if telegram_bot_service is not None:
            await telegram_bot_service.start()

        # Start Discord bot
        if discord_bot is not None:
            discord_task = asyncio.create_task(discord_bot.start_bot())
            logger.info("discord_bot_started")

        # Wait for shutdown signal
        await shutdown_event.wait()

    except KeyboardInterrupt:
        _signal_handler()
    finally:
        logger.info("arbot_shutting_down")

        # Stop funding rate manager
        if funding_manager is not None:
            await funding_manager.stop()
            fstats = funding_manager.get_stats()
            logger.info(
                "funding_rate_report",
                positions_opened=fstats.total_positions_opened,
                positions_closed=fstats.total_positions_closed,
                funding_collected=fstats.total_funding_collected,
                fees_paid=fstats.total_fees_paid,
                net_pnl=fstats.total_net_pnl,
            )

        # Stop Telegram interactive bot
        if telegram_bot_service is not None:
            await telegram_bot_service.stop()

        # Stop Discord bot
        if discord_bot is not None:
            await discord_bot.close()
            if discord_task is not None:
                discord_task.cancel()
                try:
                    await discord_task
                except asyncio.CancelledError:
                    pass
            logger.info("discord_bot_stopped")

        # Stop simulator
        await simulator.stop()
        report = simulator.get_report()
        logger.info(
            "simulation_report",
            duration_seconds=report.duration_seconds,
            cycles_run=report.pipeline_stats.cycles_run,
            signals_detected=report.pipeline_stats.total_signals_detected,
            signals_executed=report.pipeline_stats.total_signals_executed,
            pnl_usd=report.final_pnl_usd,
            fees_usd=report.total_fees_usd,
            win_rate=report.win_rate,
            trade_count=report.trade_count,
        )

        # Stop price collector
        await collector.stop()

        # Disconnect Redis
        await redis_cache.disconnect()

        logger.info("arbot_stopped")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="ArBot - Cross-Exchange Crypto Arbitrage Bot",
    )
    parser.add_argument(
        "--config-dir",
        default="configs",
        help="Path to configuration directory (default: configs)",
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "backtest"],
        default=None,
        help="Execution mode override (default: from config)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    args = parse_args(argv)

    # Load configuration
    config = load_config(config_dir=args.config_dir)

    # Apply mode override if specified
    if args.mode is not None:
        config.system.execution_mode = ExecutionMode(args.mode)

    # Setup logging
    setup_logging(log_level=config.system.log_level)

    # Run the system
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
