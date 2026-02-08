"""Unit tests for the price collection orchestrator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arbot.connectors.base import BaseConnector, ConnectionState
from arbot.core.collector import PriceCollector
from arbot.models import (
    AssetBalance,
    ExchangeInfo,
    Order,
    OrderBook,
    OrderBookEntry,
    OrderSide,
    OrderStatus,
    OrderType,
    TradingFee,
    TradeResult,
)
from arbot.storage.redis_cache import RedisCache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_connector(name: str) -> MagicMock:
    """Create a mock BaseConnector with the given exchange name."""
    connector = MagicMock(spec=BaseConnector)
    connector.exchange_name = name
    connector.connect = AsyncMock()
    connector.disconnect = AsyncMock()
    connector.subscribe_orderbook = AsyncMock()
    connector.subscribe_trades = AsyncMock()
    connector.on_orderbook_update = MagicMock()
    connector.on_trade_update = MagicMock()
    connector.is_connected = True
    return connector


def _make_mock_redis() -> MagicMock:
    """Create a mock RedisCache."""
    cache = MagicMock(spec=RedisCache)
    cache.set_orderbook = AsyncMock()
    cache.publish_price_update = AsyncMock()
    return cache


def _make_orderbook(exchange: str = "binance", symbol: str = "BTC/USDT") -> OrderBook:
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        timestamp=1700000000.0,
        bids=[OrderBookEntry(price=50000.0, quantity=1.5)],
        asks=[OrderBookEntry(price=50001.0, quantity=1.0)],
    )


def _make_trade_result(exchange: str = "binance", symbol: str = "BTC/USDT") -> TradeResult:
    order = Order(
        id="trade_1",
        exchange=exchange,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.5,
        price=50000.0,
        status=OrderStatus.FILLED,
    )
    return TradeResult(
        order=order,
        filled_quantity=0.5,
        filled_price=50000.0,
        fee=0.05,
        fee_asset="USDT",
        latency_ms=10.0,
    )


@pytest.fixture
def mock_connectors() -> list[MagicMock]:
    return [
        _make_mock_connector("binance"),
        _make_mock_connector("upbit"),
    ]


@pytest.fixture
def mock_redis() -> MagicMock:
    return _make_mock_redis()


@pytest.fixture
def collector(mock_connectors, mock_redis) -> PriceCollector:
    return PriceCollector(
        connectors=mock_connectors,
        redis_cache=mock_redis,
        symbols=["BTC/USDT", "ETH/USDT"],
    )


# ---------------------------------------------------------------------------
# Start/Stop tests
# ---------------------------------------------------------------------------


class TestStartStop:
    """Tests for collector lifecycle management."""

    @pytest.mark.asyncio
    async def test_start_connects_all_exchanges(
        self, collector: PriceCollector, mock_connectors: list[MagicMock]
    ) -> None:
        await collector.start()

        for c in mock_connectors:
            c.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_subscribes_orderbooks_and_trades(
        self, collector: PriceCollector, mock_connectors: list[MagicMock]
    ) -> None:
        await collector.start()

        for c in mock_connectors:
            c.subscribe_orderbook.assert_called_once_with(
                ["BTC/USDT", "ETH/USDT"], depth=10
            )
            c.subscribe_trades.assert_called_once_with(
                ["BTC/USDT", "ETH/USDT"]
            )

    @pytest.mark.asyncio
    async def test_start_registers_callbacks(
        self, collector: PriceCollector, mock_connectors: list[MagicMock]
    ) -> None:
        await collector.start()

        for c in mock_connectors:
            c.on_orderbook_update.assert_called_once()
            c.on_trade_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_disconnects_all_exchanges(
        self, collector: PriceCollector, mock_connectors: list[MagicMock]
    ) -> None:
        await collector.start()
        await collector.stop()

        for c in mock_connectors:
            c.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_handles_connection_failure(
        self, mock_redis: MagicMock
    ) -> None:
        good = _make_mock_connector("binance")
        bad = _make_mock_connector("bad_exchange")
        bad.connect = AsyncMock(side_effect=ConnectionError("fail"))

        collector = PriceCollector(
            connectors=[good, bad],
            redis_cache=mock_redis,
            symbols=["BTC/USDT"],
        )

        await collector.start()

        # Good exchange should still be connected
        good.connect.assert_called_once()
        status = collector.get_status()
        assert status["exchanges"]["binance"]["connected"] is True
        assert status["exchanges"]["bad_exchange"]["connected"] is False

    @pytest.mark.asyncio
    async def test_stop_handles_disconnect_failure(
        self, mock_redis: MagicMock
    ) -> None:
        c = _make_mock_connector("binance")
        c.disconnect = AsyncMock(side_effect=Exception("disconnect error"))

        collector = PriceCollector(
            connectors=[c],
            redis_cache=mock_redis,
            symbols=["BTC/USDT"],
        )

        await collector.start()
        # Should not raise
        await collector.stop()


# ---------------------------------------------------------------------------
# Callback tests
# ---------------------------------------------------------------------------


class TestOrderbookCallback:
    """Tests for order book update handling."""

    @pytest.mark.asyncio
    async def test_on_orderbook_stores_in_redis(
        self, collector: PriceCollector, mock_redis: MagicMock
    ) -> None:
        ob = _make_orderbook("binance", "BTC/USDT")
        await collector._on_orderbook_update(ob)

        mock_redis.set_orderbook.assert_called_once_with("binance", "BTC/USDT", ob)

    @pytest.mark.asyncio
    async def test_on_orderbook_publishes_event(
        self, collector: PriceCollector, mock_redis: MagicMock
    ) -> None:
        ob = _make_orderbook("upbit", "ETH/USDT")
        await collector._on_orderbook_update(ob)

        mock_redis.publish_price_update.assert_called_once_with("upbit", "ETH/USDT", ob)

    @pytest.mark.asyncio
    async def test_on_orderbook_updates_stats(
        self, collector: PriceCollector
    ) -> None:
        ob = _make_orderbook("binance", "BTC/USDT")
        await collector._on_orderbook_update(ob)
        await collector._on_orderbook_update(ob)

        status = collector.get_status()
        assert status["exchanges"]["binance"]["orderbook_count"] == 2
        assert status["exchanges"]["binance"]["last_orderbook_update"] is not None

    @pytest.mark.asyncio
    async def test_on_orderbook_handles_redis_error(
        self, collector: PriceCollector, mock_redis: MagicMock
    ) -> None:
        mock_redis.set_orderbook = AsyncMock(side_effect=Exception("redis down"))

        ob = _make_orderbook("binance", "BTC/USDT")
        # Should not raise
        await collector._on_orderbook_update(ob)


class TestTradeCallback:
    """Tests for trade update handling."""

    @pytest.mark.asyncio
    async def test_on_trade_updates_stats(
        self, collector: PriceCollector
    ) -> None:
        tr = _make_trade_result("binance", "BTC/USDT")
        await collector._on_trade_update(tr)
        await collector._on_trade_update(tr)
        await collector._on_trade_update(tr)

        status = collector.get_status()
        assert status["exchanges"]["binance"]["trade_count"] == 3
        assert status["exchanges"]["binance"]["last_trade_update"] is not None


# ---------------------------------------------------------------------------
# Status tests
# ---------------------------------------------------------------------------


class TestStatus:
    """Tests for collector status reporting."""

    def test_initial_status(self, collector: PriceCollector) -> None:
        status = collector.get_status()
        assert status["running"] is False
        assert "binance" in status["exchanges"]
        assert "upbit" in status["exchanges"]
        assert status["exchanges"]["binance"]["connected"] is False
        assert status["exchanges"]["binance"]["orderbook_count"] == 0
        assert status["exchanges"]["binance"]["trade_count"] == 0
        assert status["exchanges"]["binance"]["last_orderbook_update"] is None

    @pytest.mark.asyncio
    async def test_status_after_start(
        self, collector: PriceCollector
    ) -> None:
        await collector.start()
        status = collector.get_status()
        assert status["running"] is True
        assert status["exchanges"]["binance"]["connected"] is True
        assert status["exchanges"]["upbit"]["connected"] is True

    @pytest.mark.asyncio
    async def test_status_after_stop(
        self, collector: PriceCollector
    ) -> None:
        await collector.start()
        await collector.stop()
        status = collector.get_status()
        assert status["running"] is False
        assert status["exchanges"]["binance"]["connected"] is False


# ---------------------------------------------------------------------------
# Context manager tests
# ---------------------------------------------------------------------------


class TestContextManager:
    """Tests for async context manager support."""

    @pytest.mark.asyncio
    async def test_context_manager(
        self, mock_connectors: list[MagicMock], mock_redis: MagicMock
    ) -> None:
        collector = PriceCollector(
            connectors=mock_connectors,
            redis_cache=mock_redis,
            symbols=["BTC/USDT"],
        )

        async with collector as c:
            status = c.get_status()
            assert status["running"] is True

        status = collector.get_status()
        assert status["running"] is False

    @pytest.mark.asyncio
    async def test_custom_orderbook_depth(
        self, mock_redis: MagicMock
    ) -> None:
        c = _make_mock_connector("binance")
        collector = PriceCollector(
            connectors=[c],
            redis_cache=mock_redis,
            symbols=["BTC/USDT"],
            orderbook_depth=20,
        )

        await collector.start()
        c.subscribe_orderbook.assert_called_once_with(["BTC/USDT"], depth=20)
        await collector.stop()
