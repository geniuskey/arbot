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

from arbot.alerts.notifier_protocol import Notifier
from arbot.config import AppConfig, ExecutionMode, ExchangeConfig, load_config
from arbot.connectors.base import BaseConnector
from arbot.connectors.binance import BinanceConnector
from arbot.connectors.upbit import UpbitConnector
from arbot.core.collector import PriceCollector
from arbot.core.pipeline import ArbitragePipeline
from arbot.core.simulator import PaperTradingSimulator
from arbot.detector.spatial import SpatialDetector
from arbot.execution.paper_executor import PaperExecutor
from arbot.logging import get_logger, setup_logging
from arbot.models.config import ExchangeInfo, RiskConfig, TradingFee
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

        connector = connector_cls(
            config=info,
            api_key=api_key,
            api_secret=api_secret,
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

    Assigns a default balance of 10,000 USDT per exchange for paper trading.

    Args:
        config: Application configuration.

    Returns:
        Mapping of exchange name to {asset: amount}.
    """
    balances: dict[str, dict[str, float]] = {}
    for name in config.exchanges_enabled:
        balances[name] = {"USDT": 10_000.0}
    return balances


async def run(config: AppConfig) -> None:
    """Run the ArBot system.

    Assembles all components, starts price collection, and runs the
    simulation pipeline until interrupted.

    Args:
        config: Validated application configuration.
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

    # Create price collector
    collector = PriceCollector(
        connectors=connectors,
        redis_cache=redis_cache,
        symbols=config.symbols,
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
        )
        logger.info("spatial_detector_enabled")

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
    )

    # Create simulator with orderbook provider from Redis
    async def orderbook_provider() -> dict:
        """Fetch latest orderbooks from Redis for all enabled symbols."""
        all_obs: dict = {}
        for symbol in config.symbols:
            obs = await redis_cache.get_all_orderbooks(symbol)
            all_obs.update(obs)
        return all_obs

    simulator = PaperTradingSimulator(pipeline=pipeline, interval_seconds=1.0)

    # Setup notification channels
    notifiers: list[Notifier] = []
    discord_bot: ArBotDiscord | None = None
    discord_task: asyncio.Task[None] | None = None

    # Telegram notifier (if configured)
    if config.alerts.telegram.enabled and config.alerts.telegram.bot_token:
        from arbot.alerts.telegram import TelegramNotifier

        telegram_notifier = TelegramNotifier(
            bot_token=config.alerts.telegram.bot_token,
            chat_id=config.alerts.telegram.chat_id,
        )
        notifiers.append(telegram_notifier)
        logger.info("telegram_notifier_enabled")

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
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown_signal_received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        # Connect Redis
        await redis_cache.connect()
        logger.info("redis_connected")

        # Start price collection
        await collector.start()
        logger.info("price_collector_started")

        # Start simulator
        await simulator.start(orderbook_provider=orderbook_provider)
        logger.info(
            "simulator_started",
            mode=config.system.execution_mode.value,
            symbols=config.symbols,
            exchanges=[c.exchange_name for c in connectors],
        )

        # Start Discord bot
        if discord_bot is not None:
            discord_task = asyncio.create_task(discord_bot.start_bot())
            logger.info("discord_bot_started")

        # Wait for shutdown signal
        await shutdown_event.wait()

    finally:
        logger.info("arbot_shutting_down")

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
