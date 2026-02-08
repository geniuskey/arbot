"""Unit tests for the Redis cache module using fakeredis."""

import asyncio
import json

import fakeredis.aioredis
import pytest

from arbot.models import AssetBalance, OrderBook, OrderBookEntry
from arbot.storage.redis_cache import RedisCache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client():
    """Create a fakeredis async client."""
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.aclose()


@pytest.fixture
async def cache(redis_client):
    """Create a RedisCache with fakeredis backend."""
    c = RedisCache(client=redis_client, ttl=30)
    await c.connect()
    yield c
    await c.disconnect()


def _make_orderbook(exchange: str = "binance", symbol: str = "BTC/USDT") -> OrderBook:
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        timestamp=1700000000.0,
        bids=[
            OrderBookEntry(price=50000.0, quantity=1.5),
            OrderBookEntry(price=49999.0, quantity=2.0),
        ],
        asks=[
            OrderBookEntry(price=50001.0, quantity=1.0),
            OrderBookEntry(price=50002.0, quantity=3.0),
        ],
    )


# ---------------------------------------------------------------------------
# OrderBook cache tests
# ---------------------------------------------------------------------------


class TestOrderBookCache:
    """Tests for order book storage and retrieval."""

    @pytest.mark.asyncio
    async def test_set_and_get_orderbook(self, cache: RedisCache) -> None:
        ob = _make_orderbook()
        await cache.set_orderbook("binance", "BTC/USDT", ob)

        result = await cache.get_orderbook("binance", "BTC/USDT")
        assert result is not None
        assert result.exchange == "binance"
        assert result.symbol == "BTC/USDT"
        assert result.timestamp == 1700000000.0
        assert len(result.bids) == 2
        assert len(result.asks) == 2
        assert result.bids[0].price == 50000.0
        assert result.bids[0].quantity == 1.5
        assert result.asks[0].price == 50001.0

    @pytest.mark.asyncio
    async def test_get_nonexistent_orderbook(self, cache: RedisCache) -> None:
        result = await cache.get_orderbook("unknown", "XYZ/USDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_overwrite_orderbook(self, cache: RedisCache) -> None:
        ob1 = _make_orderbook()
        await cache.set_orderbook("binance", "BTC/USDT", ob1)

        ob2 = OrderBook(
            exchange="binance",
            symbol="BTC/USDT",
            timestamp=1700000001.0,
            bids=[OrderBookEntry(price=50100.0, quantity=1.0)],
            asks=[OrderBookEntry(price=50200.0, quantity=1.0)],
        )
        await cache.set_orderbook("binance", "BTC/USDT", ob2)

        result = await cache.get_orderbook("binance", "BTC/USDT")
        assert result is not None
        assert result.timestamp == 1700000001.0
        assert result.bids[0].price == 50100.0

    @pytest.mark.asyncio
    async def test_different_exchanges_stored_separately(
        self, cache: RedisCache
    ) -> None:
        ob_binance = _make_orderbook("binance", "BTC/USDT")
        ob_upbit = OrderBook(
            exchange="upbit",
            symbol="BTC/USDT",
            timestamp=1700000000.0,
            bids=[OrderBookEntry(price=49000.0, quantity=1.0)],
            asks=[OrderBookEntry(price=49100.0, quantity=1.0)],
        )

        await cache.set_orderbook("binance", "BTC/USDT", ob_binance)
        await cache.set_orderbook("upbit", "BTC/USDT", ob_upbit)

        r1 = await cache.get_orderbook("binance", "BTC/USDT")
        r2 = await cache.get_orderbook("upbit", "BTC/USDT")

        assert r1 is not None
        assert r2 is not None
        assert r1.bids[0].price == 50000.0
        assert r2.bids[0].price == 49000.0


class TestGetAllOrderbooks:
    """Tests for retrieving order books across all exchanges."""

    @pytest.mark.asyncio
    async def test_get_all_orderbooks(self, cache: RedisCache) -> None:
        ob1 = _make_orderbook("binance", "BTC/USDT")
        ob2 = OrderBook(
            exchange="upbit",
            symbol="BTC/USDT",
            timestamp=1700000000.0,
            bids=[OrderBookEntry(price=49000.0, quantity=1.0)],
            asks=[OrderBookEntry(price=49100.0, quantity=1.0)],
        )

        await cache.set_orderbook("binance", "BTC/USDT", ob1)
        await cache.set_orderbook("upbit", "BTC/USDT", ob2)

        result = await cache.get_all_orderbooks("BTC/USDT")
        assert len(result) == 2
        assert "binance" in result
        assert "upbit" in result
        assert result["binance"].bids[0].price == 50000.0
        assert result["upbit"].bids[0].price == 49000.0

    @pytest.mark.asyncio
    async def test_get_all_orderbooks_empty(self, cache: RedisCache) -> None:
        result = await cache.get_all_orderbooks("XYZ/USDT")
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_all_orderbooks_filters_by_symbol(
        self, cache: RedisCache
    ) -> None:
        ob_btc = _make_orderbook("binance", "BTC/USDT")
        ob_eth = OrderBook(
            exchange="binance",
            symbol="ETH/USDT",
            timestamp=1700000000.0,
            bids=[OrderBookEntry(price=3000.0, quantity=10.0)],
            asks=[OrderBookEntry(price=3001.0, quantity=10.0)],
        )

        await cache.set_orderbook("binance", "BTC/USDT", ob_btc)
        await cache.set_orderbook("binance", "ETH/USDT", ob_eth)

        result = await cache.get_all_orderbooks("BTC/USDT")
        assert len(result) == 1
        assert "binance" in result
        assert result["binance"].symbol == "BTC/USDT"


# ---------------------------------------------------------------------------
# Pub/Sub tests
# ---------------------------------------------------------------------------


class TestPubSub:
    """Tests for Redis Pub/Sub price update events."""

    @pytest.mark.asyncio
    async def test_publish_price_update(
        self, cache: RedisCache, redis_client
    ) -> None:
        ob = _make_orderbook()

        # Set up a subscriber to verify the message
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("arbot:price_updates")

        # Consume the subscribe confirmation
        msg = await pubsub.get_message(timeout=1.0)

        await cache.publish_price_update("binance", "BTC/USDT", ob)

        msg = await pubsub.get_message(timeout=1.0)
        assert msg is not None
        assert msg["type"] == "message"

        data = json.loads(msg["data"])
        assert data["exchange"] == "binance"
        assert data["symbol"] == "BTC/USDT"
        assert data["best_bid"] == 50000.0
        assert data["best_ask"] == 50001.0
        assert data["mid_price"] == 50000.5

        await pubsub.unsubscribe()
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# Balance cache tests
# ---------------------------------------------------------------------------


class TestBalanceCache:
    """Tests for balance storage and retrieval."""

    @pytest.mark.asyncio
    async def test_set_and_get_balance(self, cache: RedisCache) -> None:
        balances = {
            "BTC": AssetBalance(asset="BTC", free=1.0, locked=0.5, usd_value=75000.0),
            "USDT": AssetBalance(asset="USDT", free=10000.0, locked=0.0, usd_value=10000.0),
        }

        await cache.set_balance("binance", balances)
        result = await cache.get_balance("binance")

        assert result is not None
        assert "BTC" in result
        assert "USDT" in result
        assert result["BTC"].free == 1.0
        assert result["BTC"].locked == 0.5
        assert result["BTC"].usd_value == 75000.0
        assert result["USDT"].free == 10000.0

    @pytest.mark.asyncio
    async def test_get_nonexistent_balance(self, cache: RedisCache) -> None:
        result = await cache.get_balance("unknown")
        assert result is None

    @pytest.mark.asyncio
    async def test_balance_usd_value_none(self, cache: RedisCache) -> None:
        balances = {
            "ETH": AssetBalance(asset="ETH", free=5.0, locked=0.0, usd_value=None),
        }

        await cache.set_balance("okx", balances)
        result = await cache.get_balance("okx")

        assert result is not None
        assert result["ETH"].usd_value is None


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------


class TestConnection:
    """Tests for Redis connection lifecycle."""

    @pytest.mark.asyncio
    async def test_not_connected_raises(self) -> None:
        cache = RedisCache()
        with pytest.raises(ConnectionError, match="Redis not connected"):
            await cache.get_orderbook("binance", "BTC/USDT")

    @pytest.mark.asyncio
    async def test_context_manager(self, redis_client) -> None:
        cache = RedisCache(client=redis_client, ttl=30)
        async with cache as c:
            ob = _make_orderbook()
            await c.set_orderbook("binance", "BTC/USDT", ob)
            result = await c.get_orderbook("binance", "BTC/USDT")
            assert result is not None
