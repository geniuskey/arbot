"""Unit tests for the Binance connector module."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arbot.connectors.binance import (
    BinanceConnector,
    _to_binance_symbol,
    _to_unified_symbol,
)
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def binance_config() -> ExchangeInfo:
    return ExchangeInfo(
        name="binance",
        tier=1,
        is_active=True,
        fees=TradingFee(maker_pct=0.10, taker_pct=0.10),
        rate_limit={"type": "weight", "limit": 1200, "window": 60},
    )


@pytest.fixture
def connector(binance_config: ExchangeInfo) -> BinanceConnector:
    return BinanceConnector(config=binance_config, api_key="test_key", api_secret="test_secret")


# ---------------------------------------------------------------------------
# Symbol conversion tests
# ---------------------------------------------------------------------------


class TestSymbolConversion:
    """Tests for Binance symbol format conversion."""

    def test_to_binance_symbol_btc_usdt(self) -> None:
        assert _to_binance_symbol("BTC/USDT") == "btcusdt"

    def test_to_binance_symbol_eth_btc(self) -> None:
        assert _to_binance_symbol("ETH/BTC") == "ethbtc"

    def test_to_binance_symbol_sol_usdt(self) -> None:
        assert _to_binance_symbol("SOL/USDT") == "solusdt"

    def test_to_binance_symbol_lowercase(self) -> None:
        assert _to_binance_symbol("doge/usdt") == "dogeusdt"

    def test_to_unified_symbol_btcusdt(self) -> None:
        assert _to_unified_symbol("btcusdt") == "BTC/USDT"

    def test_to_unified_symbol_ethbtc(self) -> None:
        assert _to_unified_symbol("ethbtc") == "ETH/BTC"

    def test_to_unified_symbol_solusdt(self) -> None:
        assert _to_unified_symbol("solusdt") == "SOL/USDT"

    def test_to_unified_symbol_bnbusdt(self) -> None:
        assert _to_unified_symbol("bnbusdt") == "BNB/USDT"

    def test_to_unified_symbol_ethusdc(self) -> None:
        assert _to_unified_symbol("ethusdc") == "ETH/USDC"

    def test_to_unified_symbol_unknown(self) -> None:
        # Falls through all quote matches, returns uppercase
        assert _to_unified_symbol("xyzabc") == "XYZABC"


# ---------------------------------------------------------------------------
# WebSocket message parsing tests
# ---------------------------------------------------------------------------


class TestOrderBookParsing:
    """Tests for parsing Binance WebSocket order book messages."""

    @pytest.mark.asyncio
    async def test_handle_depth_update(self, connector: BinanceConnector) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        depth_msg = {
            "e": "depthUpdate",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "b": [
                ["50000.00", "1.5"],
                ["49999.00", "2.0"],
            ],
            "a": [
                ["50001.00", "1.0"],
                ["50002.00", "3.0"],
            ],
        }

        await connector._handle_ws_message(depth_msg)

        assert len(received) == 1
        ob = received[0]
        assert ob.exchange == "binance"
        assert ob.symbol == "BTC/USDT"
        assert ob.timestamp == 1700000000.0
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        assert ob.bids[0].price == 50000.0
        assert ob.bids[0].quantity == 1.5
        assert ob.bids[1].price == 49999.0
        assert ob.asks[0].price == 50001.0
        assert ob.asks[1].price == 50002.0

    @pytest.mark.asyncio
    async def test_handle_depth_update_filters_zero_qty(
        self, connector: BinanceConnector
    ) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        depth_msg = {
            "e": "depthUpdate",
            "E": 1700000000000,
            "s": "ETHUSDT",
            "b": [
                ["3000.00", "0"],
                ["2999.00", "5.0"],
            ],
            "a": [
                ["3001.00", "2.0"],
                ["3002.00", "0"],
            ],
        }

        await connector._handle_ws_message(depth_msg)

        assert len(received) == 1
        ob = received[0]
        assert len(ob.bids) == 1
        assert ob.bids[0].price == 2999.0
        assert len(ob.asks) == 1
        assert ob.asks[0].price == 3001.0

    @pytest.mark.asyncio
    async def test_handle_partial_depth(self, connector: BinanceConnector) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        # Combined stream format
        combined_msg = {
            "stream": "btcusdt@depth10@100ms",
            "data": {
                "lastUpdateId": 123456789,
                "bids": [
                    ["50000.00", "1.0"],
                    ["49999.50", "2.5"],
                ],
                "asks": [
                    ["50000.50", "0.8"],
                    ["50001.00", "1.2"],
                ],
            },
        }

        await connector._handle_ws_message(combined_msg)

        assert len(received) == 1
        ob = received[0]
        assert ob.exchange == "binance"
        assert ob.symbol == "BTC/USDT"
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        assert ob.bids[0].price == 50000.0
        assert ob.asks[0].price == 50000.5

    @pytest.mark.asyncio
    async def test_handle_depth_sorts_correctly(
        self, connector: BinanceConnector
    ) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        # Send bids and asks in unsorted order
        depth_msg = {
            "e": "depthUpdate",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "b": [
                ["49000.00", "1.0"],
                ["50000.00", "2.0"],
                ["49500.00", "1.5"],
            ],
            "a": [
                ["51000.00", "1.0"],
                ["50100.00", "2.0"],
                ["50500.00", "1.5"],
            ],
        }

        await connector._handle_ws_message(depth_msg)

        ob = received[0]
        # Bids should be descending
        assert ob.bids[0].price == 50000.0
        assert ob.bids[1].price == 49500.0
        assert ob.bids[2].price == 49000.0
        # Asks should be ascending
        assert ob.asks[0].price == 50100.0
        assert ob.asks[1].price == 50500.0
        assert ob.asks[2].price == 51000.0


class TestTradeParsing:
    """Tests for parsing Binance WebSocket trade messages."""

    @pytest.mark.asyncio
    async def test_handle_trade_buy(self, connector: BinanceConnector) -> None:
        received: list[TradeResult] = []

        async def on_trade(tr: TradeResult) -> None:
            received.append(tr)

        connector.on_trade_update(on_trade)

        trade_msg = {
            "e": "trade",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "t": 12345,
            "p": "50000.00",
            "q": "0.5",
            "T": 1700000000000,
            "m": False,  # buyer is taker = BUY
        }

        await connector._handle_ws_message(trade_msg)

        assert len(received) == 1
        tr = received[0]
        assert tr.order.exchange == "binance"
        assert tr.order.symbol == "BTC/USDT"
        assert tr.order.side == OrderSide.BUY
        assert tr.filled_price == 50000.0
        assert tr.filled_quantity == 0.5

    @pytest.mark.asyncio
    async def test_handle_trade_sell(self, connector: BinanceConnector) -> None:
        received: list[TradeResult] = []

        async def on_trade(tr: TradeResult) -> None:
            received.append(tr)

        connector.on_trade_update(on_trade)

        trade_msg = {
            "e": "trade",
            "E": 1700000000000,
            "s": "ETHUSDT",
            "t": 67890,
            "p": "3000.00",
            "q": "10.0",
            "T": 1700000000000,
            "m": True,  # buyer is maker = taker was selling = SELL
        }

        await connector._handle_ws_message(trade_msg)

        assert len(received) == 1
        tr = received[0]
        assert tr.order.side == OrderSide.SELL
        assert tr.order.symbol == "ETH/USDT"
        assert tr.filled_price == 3000.0
        assert tr.filled_quantity == 10.0

    @pytest.mark.asyncio
    async def test_non_dict_message_ignored(self, connector: BinanceConnector) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        await connector._handle_ws_message("not a dict")
        assert len(received) == 0


# ---------------------------------------------------------------------------
# REST API tests (mocked)
# ---------------------------------------------------------------------------


class TestPlaceOrder:
    """Tests for Binance order placement via ccxt."""

    @pytest.mark.asyncio
    async def test_place_limit_order(self, connector: BinanceConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.create_order = AsyncMock(return_value={
            "id": "order_123",
            "status": "open",
            "symbol": "BTC/USDT",
        })
        connector._exchange = mock_exchange

        order = await connector.place_order(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.1,
            price=50000.0,
        )

        assert order.id == "order_123"
        assert order.exchange == "binance"
        assert order.side == OrderSide.BUY
        assert order.status == OrderStatus.SUBMITTED
        mock_exchange.create_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_place_market_order(self, connector: BinanceConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.create_order = AsyncMock(return_value={
            "id": "order_456",
            "status": "closed",
        })
        connector._exchange = mock_exchange

        order = await connector.place_order(
            symbol="ETH/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=5.0,
        )

        assert order.id == "order_456"
        assert order.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_place_ioc_order_passes_time_in_force(
        self, connector: BinanceConnector
    ) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.create_order = AsyncMock(return_value={
            "id": "order_789",
            "status": "open",
        })
        connector._exchange = mock_exchange

        await connector.place_order(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.IOC,
            quantity=0.5,
            price=49000.0,
        )

        call_kwargs = mock_exchange.create_order.call_args
        assert call_kwargs[1]["params"] == {"timeInForce": "IOC"}

    @pytest.mark.asyncio
    async def test_place_limit_order_without_price_raises(
        self, connector: BinanceConnector
    ) -> None:
        connector._exchange = AsyncMock()

        with pytest.raises(ValueError, match="Price is required"):
            await connector.place_order(
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=0.1,
            )

    @pytest.mark.asyncio
    async def test_place_order_not_connected_raises(
        self, connector: BinanceConnector
    ) -> None:
        with pytest.raises(ConnectionError, match="Not connected"):
            await connector.place_order(
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.1,
            )


class TestCancelOrder:
    """Tests for Binance order cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_order_success(self, connector: BinanceConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.cancel_order = AsyncMock(return_value={"status": "canceled"})
        connector._exchange = mock_exchange

        result = await connector.cancel_order("order_123", "BTC/USDT")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_order_not_connected(
        self, connector: BinanceConnector
    ) -> None:
        with pytest.raises(ConnectionError):
            await connector.cancel_order("order_123", "BTC/USDT")


class TestGetBalances:
    """Tests for Binance balance queries."""

    @pytest.mark.asyncio
    async def test_get_balances(self, connector: BinanceConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.fetch_balance = AsyncMock(return_value={
            "total": {"BTC": 1.5, "USDT": 10000.0, "ETH": 0.0},
            "free": {"BTC": 1.0, "USDT": 8000.0, "ETH": 0.0},
            "used": {"BTC": 0.5, "USDT": 2000.0, "ETH": 0.0},
        })
        connector._exchange = mock_exchange

        balances = await connector.get_balances()

        assert "BTC" in balances
        assert "USDT" in balances
        assert "ETH" not in balances  # zero balance filtered out

        btc = balances["BTC"]
        assert btc.asset == "BTC"
        assert btc.free == 1.0
        assert btc.locked == 0.5
        assert btc.total == 1.5


class TestGetTradingFee:
    """Tests for Binance trading fee queries."""

    @pytest.mark.asyncio
    async def test_get_trading_fee(self, connector: BinanceConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.fetch_trading_fee = AsyncMock(return_value={
            "maker": 0.0010,
            "taker": 0.0010,
        })
        connector._exchange = mock_exchange

        fee = await connector.get_trading_fee("BTC/USDT")
        assert fee.maker_pct == pytest.approx(0.10)
        assert fee.taker_pct == pytest.approx(0.10)


class TestGetOrderStatus:
    """Tests for Binance order status queries."""

    @pytest.mark.asyncio
    async def test_get_order_status_filled(self, connector: BinanceConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.fetch_order = AsyncMock(return_value={
            "id": "order_123",
            "status": "closed",
            "side": "buy",
            "type": "limit",
            "amount": 0.5,
            "price": 50000.0,
        })
        connector._exchange = mock_exchange

        order = await connector.get_order_status("order_123", "BTC/USDT")
        assert order.id == "order_123"
        assert order.status == OrderStatus.FILLED
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.LIMIT
        assert order.quantity == 0.5
        assert order.price == 50000.0


class TestConnectDisconnect:
    """Tests for Binance connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_creates_exchange(
        self, connector: BinanceConnector
    ) -> None:
        with patch("arbot.connectors.binance.ccxt") as mock_ccxt:
            mock_instance = AsyncMock()
            mock_ccxt.binance = MagicMock(return_value=mock_instance)

            await connector.connect()

            assert connector.is_connected
            mock_ccxt.binance.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self, connector: BinanceConnector) -> None:
        mock_exchange = AsyncMock()
        connector._exchange = mock_exchange
        connector._set_state(connector.state.__class__("CONNECTED"))

        await connector.disconnect()

        assert not connector.is_connected
        mock_exchange.close.assert_called_once()
        assert connector._exchange is None
