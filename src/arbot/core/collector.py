"""Price collection orchestrator for multi-exchange concurrent data gathering.

Manages simultaneous connections to multiple exchanges, subscribes to order book
and trade streams, and stores updates in Redis cache with Pub/Sub notifications.
"""

import asyncio
import time

from arbot.connectors.base import BaseConnector
from arbot.logging import get_logger
from arbot.models import OrderBook, TradeResult
from arbot.storage.redis_cache import RedisCache


class PriceCollector:
    """Orchestrates price collection from multiple exchanges.

    Connects to all configured exchange connectors, subscribes to order book
    and trade streams for the specified symbols, and routes updates to Redis
    cache with Pub/Sub event publishing.

    Args:
        connectors: List of exchange connectors to manage.
        redis_cache: Redis cache for storing market data.
        symbols: Trading pairs to subscribe (e.g. ["BTC/USDT", "ETH/USDT"]).
        orderbook_depth: Number of order book price levels to subscribe.
    """

    def __init__(
        self,
        connectors: list[BaseConnector],
        redis_cache: RedisCache,
        symbols: list[str],
        orderbook_depth: int = 10,
    ) -> None:
        self._connectors = connectors
        self._redis_cache = redis_cache
        self._symbols = symbols
        self._orderbook_depth = orderbook_depth
        self._logger = get_logger("price_collector")

        # Per-exchange statistics
        self._stats: dict[str, _ExchangeStats] = {
            c.exchange_name: _ExchangeStats() for c in connectors
        }

        self._running = False

    async def start(self) -> None:
        """Start price collection from all exchanges.

        Connects all connectors concurrently, registers callbacks, and
        subscribes to order book and trade streams.
        """
        self._running = True
        self._logger.info(
            "price_collector_starting",
            exchanges=[c.exchange_name for c in self._connectors],
            symbols=self._symbols,
        )

        # Register callbacks before connecting
        for connector in self._connectors:
            connector.on_orderbook_update(self._on_orderbook_update)
            connector.on_trade_update(self._on_trade_update)

        # Connect all exchanges concurrently
        connect_tasks = [
            self._connect_exchange(connector) for connector in self._connectors
        ]
        results = await asyncio.gather(*connect_tasks, return_exceptions=True)

        # Log connection results
        for connector, result in zip(self._connectors, results):
            if isinstance(result, Exception):
                self._logger.error(
                    "exchange_connect_failed",
                    exchange=connector.exchange_name,
                    error=str(result),
                )
                self._stats[connector.exchange_name].connected = False
            else:
                self._stats[connector.exchange_name].connected = True

        connected_count = sum(1 for s in self._stats.values() if s.connected)
        self._logger.info(
            "price_collector_started",
            connected=connected_count,
            total=len(self._connectors),
        )

    async def stop(self) -> None:
        """Stop price collection and disconnect all exchanges."""
        self._running = False
        self._logger.info("price_collector_stopping")

        disconnect_tasks = [
            self._disconnect_exchange(connector) for connector in self._connectors
        ]
        await asyncio.gather(*disconnect_tasks, return_exceptions=True)

        for stats in self._stats.values():
            stats.connected = False

        self._logger.info("price_collector_stopped")

    def get_status(self) -> dict:
        """Get the current status of all exchange connections.

        Returns:
            Dict with per-exchange status including connection state,
            last update timestamps, and message counts.
        """
        return {
            "running": self._running,
            "exchanges": {
                name: {
                    "connected": stats.connected,
                    "orderbook_count": stats.orderbook_count,
                    "trade_count": stats.trade_count,
                    "last_orderbook_update": stats.last_orderbook_update,
                    "last_trade_update": stats.last_trade_update,
                }
                for name, stats in self._stats.items()
            },
        }

    # --- Internal Methods ---

    async def _connect_exchange(self, connector: BaseConnector) -> None:
        """Connect a single exchange and subscribe to streams."""
        exchange = connector.exchange_name

        await connector.connect()

        # Subscribe to order books and trades concurrently
        await asyncio.gather(
            connector.subscribe_orderbook(self._symbols, depth=self._orderbook_depth),
            connector.subscribe_trades(self._symbols),
        )

        self._logger.info(
            "exchange_subscribed",
            exchange=exchange,
            symbols=self._symbols,
        )

    async def _disconnect_exchange(self, connector: BaseConnector) -> None:
        """Disconnect a single exchange."""
        try:
            await connector.disconnect()
            self._logger.info(
                "exchange_disconnected",
                exchange=connector.exchange_name,
            )
        except Exception:
            self._logger.exception(
                "exchange_disconnect_error",
                exchange=connector.exchange_name,
            )

    async def _on_orderbook_update(self, orderbook: OrderBook) -> None:
        """Handle an incoming order book update from any exchange.

        Stores the order book in Redis and publishes a price update event.

        Args:
            orderbook: The updated order book snapshot.
        """
        exchange = orderbook.exchange
        symbol = orderbook.symbol

        # Update statistics
        if exchange in self._stats:
            self._stats[exchange].orderbook_count += 1
            self._stats[exchange].last_orderbook_update = time.time()

        try:
            # Store in Redis cache and publish update concurrently
            await asyncio.gather(
                self._redis_cache.set_orderbook(exchange, symbol, orderbook),
                self._redis_cache.publish_price_update(exchange, symbol, orderbook),
            )
        except Exception:
            self._logger.exception(
                "redis_update_error",
                exchange=exchange,
                symbol=symbol,
            )

    async def _on_trade_update(self, trade: TradeResult) -> None:
        """Handle an incoming trade update from any exchange.

        Args:
            trade: The trade execution result.
        """
        exchange = trade.order.exchange

        # Update statistics
        if exchange in self._stats:
            self._stats[exchange].trade_count += 1
            self._stats[exchange].last_trade_update = time.time()

    # --- Context Manager ---

    async def __aenter__(self) -> "PriceCollector":
        await self.start()
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: object) -> None:
        await self.stop()


class _ExchangeStats:
    """Internal statistics tracker for a single exchange connection."""

    __slots__ = (
        "connected",
        "orderbook_count",
        "trade_count",
        "last_orderbook_update",
        "last_trade_update",
    )

    def __init__(self) -> None:
        self.connected: bool = False
        self.orderbook_count: int = 0
        self.trade_count: int = 0
        self.last_orderbook_update: float | None = None
        self.last_trade_update: float | None = None
