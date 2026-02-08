"""Redis-based real-time price cache and Pub/Sub event system.

Stores latest order book snapshots and account balances in Redis for
low-latency access by the opportunity detector and execution engine.
Publishes price update events via Redis Pub/Sub.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis

from arbot.logging import get_logger
from arbot.models import (
    AssetBalance,
    OrderBook,
    OrderBookEntry,
)

# Redis key patterns
_KEY_ORDERBOOK = "arbot:ob:{exchange}:{symbol}"
_KEY_BALANCE = "arbot:balance:{exchange}"
_CHANNEL_PRICE_UPDATE = "arbot:price_updates"

# Default TTL for cached data (seconds)
_DEFAULT_TTL = 30


class RedisCache:
    """Async Redis cache for real-time market data and account balances.

    Args:
        redis_url: Redis connection URL (e.g. "redis://localhost:6379/0").
        ttl: Time-to-live in seconds for cached entries.
        client: Optional pre-configured redis client (for testing).
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        ttl: int = _DEFAULT_TTL,
        client: aioredis.Redis | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._ttl = ttl
        self._client: aioredis.Redis | None = client
        self._pubsub: aioredis.client.PubSub | None = None
        self._subscribe_task: asyncio.Task | None = None
        self._logger = get_logger("redis_cache")

    async def connect(self) -> None:
        """Establish connection to Redis."""
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url)
        await self._client.ping()
        self._logger.info("redis_connected", url=self._redis_url)

    async def disconnect(self) -> None:
        """Close the Redis connection and stop subscriptions."""
        if self._subscribe_task is not None:
            self._subscribe_task.cancel()
            try:
                await self._subscribe_task
            except asyncio.CancelledError:
                pass
            self._subscribe_task = None

        if self._pubsub is not None:
            await self._pubsub.unsubscribe(_CHANNEL_PRICE_UPDATE)
            await self._pubsub.aclose()
            self._pubsub = None

        if self._client is not None:
            await self._client.aclose()
            self._client = None

        self._logger.info("redis_disconnected")

    # --- Order Book Cache ---

    async def set_orderbook(self, exchange: str, symbol: str, orderbook: OrderBook) -> None:
        """Store an order book snapshot in Redis.

        Args:
            exchange: Exchange identifier.
            symbol: Trading pair (e.g. "BTC/USDT").
            orderbook: The order book snapshot to cache.
        """
        if self._client is None:
            raise ConnectionError("Redis not connected")

        key = _KEY_ORDERBOOK.format(exchange=exchange, symbol=symbol)
        data = _serialize_orderbook(orderbook)
        await self._client.set(key, data, ex=self._ttl)

    async def get_orderbook(self, exchange: str, symbol: str) -> OrderBook | None:
        """Retrieve a cached order book snapshot.

        Args:
            exchange: Exchange identifier.
            symbol: Trading pair.

        Returns:
            The cached OrderBook, or None if not found or expired.
        """
        if self._client is None:
            raise ConnectionError("Redis not connected")

        key = _KEY_ORDERBOOK.format(exchange=exchange, symbol=symbol)
        raw = await self._client.get(key)
        if raw is None:
            return None

        return _deserialize_orderbook(raw)

    async def get_all_orderbooks(self, symbol: str) -> dict[str, OrderBook]:
        """Retrieve order books for a symbol from all exchanges.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT").

        Returns:
            Mapping of exchange name to OrderBook.
        """
        if self._client is None:
            raise ConnectionError("Redis not connected")

        pattern = _KEY_ORDERBOOK.format(exchange="*", symbol=symbol)
        result: dict[str, OrderBook] = {}

        keys = []
        async for key in self._client.scan_iter(match=pattern):
            keys.append(key)

        if not keys:
            return result

        values = await self._client.mget(keys)

        for key, raw in zip(keys, values):
            if raw is None:
                continue
            key_str = key.decode() if isinstance(key, bytes) else key
            # Extract exchange from key: arbot:ob:{exchange}:{symbol}
            parts = key_str.split(":")
            if len(parts) >= 3:
                exchange = parts[2]
                ob = _deserialize_orderbook(raw)
                if ob is not None:
                    result[exchange] = ob

        return result

    # --- Pub/Sub ---

    async def publish_price_update(
        self, exchange: str, symbol: str, orderbook: OrderBook
    ) -> None:
        """Publish an order book update event via Redis Pub/Sub.

        Args:
            exchange: Exchange identifier.
            symbol: Trading pair.
            orderbook: The updated order book.
        """
        if self._client is None:
            raise ConnectionError("Redis not connected")

        message = json.dumps({
            "exchange": exchange,
            "symbol": symbol,
            "timestamp": orderbook.timestamp,
            "best_bid": orderbook.best_bid,
            "best_ask": orderbook.best_ask,
            "mid_price": orderbook.mid_price,
            "spread_pct": orderbook.spread_pct,
        })

        await self._client.publish(_CHANNEL_PRICE_UPDATE, message)

    async def subscribe_price_updates(
        self, callback: Callable[[dict], Awaitable[None]]
    ) -> None:
        """Subscribe to price update events via Redis Pub/Sub.

        Starts a background task that listens for price updates and
        invokes the callback for each received message.

        Args:
            callback: Async function called with each price update dict.
        """
        if self._client is None:
            raise ConnectionError("Redis not connected")

        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(_CHANNEL_PRICE_UPDATE)

        self._subscribe_task = asyncio.create_task(
            self._listen_loop(callback)
        )
        self._logger.info("redis_subscribed_price_updates")

    async def _listen_loop(
        self, callback: Callable[[dict], Awaitable[None]]
    ) -> None:
        """Background loop that reads Pub/Sub messages."""
        if self._pubsub is None:
            return

        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    data = json.loads(raw)
                    await callback(data)
                except Exception:
                    self._logger.exception("redis_pubsub_callback_error")
        except asyncio.CancelledError:
            raise

    # --- Balance Cache ---

    async def set_balance(
        self, exchange: str, balances: dict[str, AssetBalance]
    ) -> None:
        """Store account balances in Redis.

        Args:
            exchange: Exchange identifier.
            balances: Mapping of asset symbol to AssetBalance.
        """
        if self._client is None:
            raise ConnectionError("Redis not connected")

        key = _KEY_BALANCE.format(exchange=exchange)
        data = json.dumps({
            asset: {
                "asset": bal.asset,
                "free": bal.free,
                "locked": bal.locked,
                "usd_value": bal.usd_value,
            }
            for asset, bal in balances.items()
        })
        await self._client.set(key, data, ex=self._ttl * 10)  # Longer TTL for balances

    async def get_balance(self, exchange: str) -> dict[str, AssetBalance] | None:
        """Retrieve cached account balances.

        Args:
            exchange: Exchange identifier.

        Returns:
            Mapping of asset symbol to AssetBalance, or None if not cached.
        """
        if self._client is None:
            raise ConnectionError("Redis not connected")

        key = _KEY_BALANCE.format(exchange=exchange)
        raw = await self._client.get(key)
        if raw is None:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode()

        parsed = json.loads(raw)
        return {
            asset: AssetBalance(
                asset=info["asset"],
                free=info["free"],
                locked=info["locked"],
                usd_value=info.get("usd_value"),
            )
            for asset, info in parsed.items()
        }

    # --- Context Manager ---

    async def __aenter__(self) -> "RedisCache":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: object) -> None:
        await self.disconnect()


# --- Serialization Helpers ---


def _serialize_orderbook(orderbook: OrderBook) -> str:
    """Serialize an OrderBook to a JSON string for Redis storage."""
    return json.dumps({
        "exchange": orderbook.exchange,
        "symbol": orderbook.symbol,
        "timestamp": orderbook.timestamp,
        "bids": [[e.price, e.quantity] for e in orderbook.bids],
        "asks": [[e.price, e.quantity] for e in orderbook.asks],
    })


def _deserialize_orderbook(raw: str | bytes) -> OrderBook | None:
    """Deserialize a JSON string from Redis into an OrderBook."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw)
        return OrderBook(
            exchange=data["exchange"],
            symbol=data["symbol"],
            timestamp=data["timestamp"],
            bids=[OrderBookEntry(price=b[0], quantity=b[1]) for b in data.get("bids", [])],
            asks=[OrderBookEntry(price=a[0], quantity=a[1]) for a in data.get("asks", [])],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
