"""Unit tests for the Upbit connector module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arbot.connectors.upbit import (
    UpbitConnector,
    _to_unified_symbol,
    _to_upbit_symbol,
)
from arbot.models import (
    AssetBalance,
    ExchangeInfo,
    OrderBook,
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
def upbit_config() -> ExchangeInfo:
    return ExchangeInfo(
        name="upbit",
        tier=2,
        is_active=True,
        fees=TradingFee(maker_pct=0.25, taker_pct=0.25),
        rate_limit={"type": "count", "limit": 10, "window": 1},
    )


@pytest.fixture
def connector(upbit_config: ExchangeInfo) -> UpbitConnector:
    return UpbitConnector(config=upbit_config, api_key="test_key", api_secret="test_secret")


# ---------------------------------------------------------------------------
# Symbol conversion tests
# ---------------------------------------------------------------------------


class TestSymbolConversion:
    """Tests for Upbit symbol format conversion."""

    def test_to_upbit_symbol_btc_krw(self) -> None:
        assert _to_upbit_symbol("BTC/KRW") == "KRW-BTC"

    def test_to_upbit_symbol_eth_krw(self) -> None:
        assert _to_upbit_symbol("ETH/KRW") == "KRW-ETH"

    def test_to_upbit_symbol_xrp_krw(self) -> None:
        assert _to_upbit_symbol("XRP/KRW") == "KRW-XRP"

    def test_to_upbit_symbol_btc_usdt(self) -> None:
        assert _to_upbit_symbol("BTC/USDT") == "USDT-BTC"

    def test_to_upbit_symbol_lowercase(self) -> None:
        assert _to_upbit_symbol("btc/krw") == "KRW-BTC"

    def test_to_upbit_symbol_no_slash(self) -> None:
        assert _to_upbit_symbol("BTCKRW") == "BTCKRW"

    def test_to_unified_symbol_krw_btc(self) -> None:
        assert _to_unified_symbol("KRW-BTC") == "BTC/KRW"

    def test_to_unified_symbol_krw_eth(self) -> None:
        assert _to_unified_symbol("KRW-ETH") == "ETH/KRW"

    def test_to_unified_symbol_usdt_btc(self) -> None:
        assert _to_unified_symbol("USDT-BTC") == "BTC/USDT"

    def test_to_unified_symbol_no_dash(self) -> None:
        assert _to_unified_symbol("KRWBTC") == "KRWBTC"


# ---------------------------------------------------------------------------
# Subscription message tests
# ---------------------------------------------------------------------------


class TestSubscriptionMessage:
    """Tests for Upbit subscription message building."""

    def test_build_orderbook_subscription(self, connector: UpbitConnector) -> None:
        connector._orderbook_symbols = {"BTC/KRW", "ETH/KRW"}
        msg = connector._build_subscription_message()

        assert msg[0] == {"ticket": connector._ticket}
        assert msg[-1] == {"format": "DEFAULT"}

        # Find the orderbook subscription
        ob_sub = next(m for m in msg if m.get("type") == "orderbook")
        codes = set(ob_sub["codes"])
        assert "KRW-BTC" in codes
        assert "KRW-ETH" in codes

    def test_build_trade_subscription(self, connector: UpbitConnector) -> None:
        connector._trade_symbols = {"BTC/KRW"}
        msg = connector._build_subscription_message()

        trade_sub = next(m for m in msg if m.get("type") == "trade")
        assert "KRW-BTC" in trade_sub["codes"]

    def test_build_combined_subscription(self, connector: UpbitConnector) -> None:
        connector._orderbook_symbols = {"BTC/KRW"}
        connector._trade_symbols = {"BTC/KRW", "ETH/KRW"}
        msg = connector._build_subscription_message()

        assert len(msg) == 4  # ticket + orderbook + trade + format
        types = [m.get("type") for m in msg if "type" in m]
        assert "orderbook" in types
        assert "trade" in types

    def test_build_empty_subscription(self, connector: UpbitConnector) -> None:
        msg = connector._build_subscription_message()
        assert len(msg) == 2  # ticket + format only


# ---------------------------------------------------------------------------
# WebSocket message parsing tests
# ---------------------------------------------------------------------------


class TestOrderBookParsing:
    """Tests for parsing Upbit WebSocket order book messages."""

    @pytest.mark.asyncio
    async def test_handle_orderbook(self, connector: UpbitConnector) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        ob_msg = {
            "type": "orderbook",
            "code": "KRW-BTC",
            "timestamp": 1700000000000,
            "orderbook_units": [
                {"ask_price": 50001000.0, "bid_price": 50000000.0,
                 "ask_size": 1.0, "bid_size": 2.0},
                {"ask_price": 50002000.0, "bid_price": 49999000.0,
                 "ask_size": 0.5, "bid_size": 1.5},
            ],
        }

        await connector._handle_ws_message(ob_msg)

        assert len(received) == 1
        ob = received[0]
        assert ob.exchange == "upbit"
        assert ob.symbol == "BTC/KRW"
        assert ob.timestamp == 1700000000.0
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        # Bids descending
        assert ob.bids[0].price == 50000000.0
        assert ob.bids[0].quantity == 2.0
        assert ob.bids[1].price == 49999000.0
        # Asks ascending
        assert ob.asks[0].price == 50001000.0
        assert ob.asks[1].price == 50002000.0

    @pytest.mark.asyncio
    async def test_handle_orderbook_filters_zero_size(
        self, connector: UpbitConnector
    ) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        ob_msg = {
            "type": "orderbook",
            "code": "KRW-ETH",
            "timestamp": 1700000000000,
            "orderbook_units": [
                {"ask_price": 3000000.0, "bid_price": 2999000.0,
                 "ask_size": 0, "bid_size": 5.0},
                {"ask_price": 3001000.0, "bid_price": 2998000.0,
                 "ask_size": 2.0, "bid_size": 0},
            ],
        }

        await connector._handle_ws_message(ob_msg)

        ob = received[0]
        assert len(ob.bids) == 1
        assert ob.bids[0].price == 2999000.0
        assert len(ob.asks) == 1
        assert ob.asks[0].price == 3001000.0

    @pytest.mark.asyncio
    async def test_handle_orderbook_sorts_correctly(
        self, connector: UpbitConnector
    ) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        ob_msg = {
            "type": "orderbook",
            "code": "KRW-BTC",
            "timestamp": 1700000000000,
            "orderbook_units": [
                {"ask_price": 50003000.0, "bid_price": 49997000.0,
                 "ask_size": 1.0, "bid_size": 1.0},
                {"ask_price": 50001000.0, "bid_price": 49999000.0,
                 "ask_size": 1.0, "bid_size": 1.0},
                {"ask_price": 50002000.0, "bid_price": 49998000.0,
                 "ask_size": 1.0, "bid_size": 1.0},
            ],
        }

        await connector._handle_ws_message(ob_msg)

        ob = received[0]
        # Bids descending
        assert ob.bids[0].price == 49999000.0
        assert ob.bids[1].price == 49998000.0
        assert ob.bids[2].price == 49997000.0
        # Asks ascending
        assert ob.asks[0].price == 50001000.0
        assert ob.asks[1].price == 50002000.0
        assert ob.asks[2].price == 50003000.0


class TestTradeParsing:
    """Tests for parsing Upbit WebSocket trade messages."""

    @pytest.mark.asyncio
    async def test_handle_trade_bid(self, connector: UpbitConnector) -> None:
        received: list[TradeResult] = []

        async def on_trade(tr: TradeResult) -> None:
            received.append(tr)

        connector.on_trade_update(on_trade)

        trade_msg = {
            "type": "trade",
            "code": "KRW-BTC",
            "trade_price": 50000000.0,
            "trade_volume": 0.5,
            "ask_bid": "BID",
            "trade_timestamp": 1700000000000,
            "sequential_id": 12345,
        }

        await connector._handle_ws_message(trade_msg)

        assert len(received) == 1
        tr = received[0]
        assert tr.order.exchange == "upbit"
        assert tr.order.symbol == "BTC/KRW"
        assert tr.order.side == OrderSide.BUY
        assert tr.filled_price == 50000000.0
        assert tr.filled_quantity == 0.5

    @pytest.mark.asyncio
    async def test_handle_trade_ask(self, connector: UpbitConnector) -> None:
        received: list[TradeResult] = []

        async def on_trade(tr: TradeResult) -> None:
            received.append(tr)

        connector.on_trade_update(on_trade)

        trade_msg = {
            "type": "trade",
            "code": "KRW-ETH",
            "trade_price": 3000000.0,
            "trade_volume": 10.0,
            "ask_bid": "ASK",
            "trade_timestamp": 1700000000000,
            "sequential_id": 67890,
        }

        await connector._handle_ws_message(trade_msg)

        assert len(received) == 1
        tr = received[0]
        assert tr.order.side == OrderSide.SELL
        assert tr.order.symbol == "ETH/KRW"
        assert tr.filled_price == 3000000.0
        assert tr.filled_quantity == 10.0

    @pytest.mark.asyncio
    async def test_non_dict_message_ignored(self, connector: UpbitConnector) -> None:
        received: list[OrderBook] = []

        async def on_ob(ob: OrderBook) -> None:
            received.append(ob)

        connector.on_orderbook_update(on_ob)

        await connector._handle_ws_message("not a dict")
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_unknown_type_ignored(self, connector: UpbitConnector) -> None:
        received_ob: list[OrderBook] = []
        received_tr: list[TradeResult] = []

        async def on_ob(ob: OrderBook) -> None:
            received_ob.append(ob)

        async def on_trade(tr: TradeResult) -> None:
            received_tr.append(tr)

        connector.on_orderbook_update(on_ob)
        connector.on_trade_update(on_trade)

        await connector._handle_ws_message({"type": "ticker", "code": "KRW-BTC"})
        assert len(received_ob) == 0
        assert len(received_tr) == 0


# ---------------------------------------------------------------------------
# REST API tests (mocked)
# ---------------------------------------------------------------------------


class TestPlaceOrder:
    """Tests for Upbit order placement via ccxt."""

    @pytest.mark.asyncio
    async def test_place_limit_order(self, connector: UpbitConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.create_order = AsyncMock(return_value={
            "id": "order_abc",
            "status": "open",
        })
        connector._exchange = mock_exchange

        order = await connector.place_order(
            symbol="BTC/KRW",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.01,
            price=50000000.0,
        )

        assert order.id == "order_abc"
        assert order.exchange == "upbit"
        assert order.side == OrderSide.BUY
        assert order.status == OrderStatus.SUBMITTED
        mock_exchange.create_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_place_market_order(self, connector: UpbitConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.create_order = AsyncMock(return_value={
            "id": "order_def",
            "status": "closed",
        })
        connector._exchange = mock_exchange

        order = await connector.place_order(
            symbol="ETH/KRW",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=5.0,
        )

        assert order.id == "order_def"
        assert order.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_place_limit_without_price_raises(
        self, connector: UpbitConnector
    ) -> None:
        connector._exchange = AsyncMock()

        with pytest.raises(ValueError, match="Price is required"):
            await connector.place_order(
                symbol="BTC/KRW",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=0.01,
            )

    @pytest.mark.asyncio
    async def test_place_order_not_connected_raises(
        self, connector: UpbitConnector
    ) -> None:
        with pytest.raises(ConnectionError, match="Not connected"):
            await connector.place_order(
                symbol="BTC/KRW",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.01,
            )


class TestCancelOrder:
    """Tests for Upbit order cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_order_success(self, connector: UpbitConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.cancel_order = AsyncMock(return_value={"status": "canceled"})
        connector._exchange = mock_exchange

        result = await connector.cancel_order("order_abc", "BTC/KRW")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_order_not_connected(self, connector: UpbitConnector) -> None:
        with pytest.raises(ConnectionError):
            await connector.cancel_order("order_abc", "BTC/KRW")


class TestGetBalances:
    """Tests for Upbit balance queries."""

    @pytest.mark.asyncio
    async def test_get_balances(self, connector: UpbitConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.fetch_balance = AsyncMock(return_value={
            "total": {"BTC": 0.5, "KRW": 5000000.0, "ETH": 0.0},
            "free": {"BTC": 0.3, "KRW": 4000000.0, "ETH": 0.0},
            "used": {"BTC": 0.2, "KRW": 1000000.0, "ETH": 0.0},
        })
        connector._exchange = mock_exchange

        balances = await connector.get_balances()

        assert "BTC" in balances
        assert "KRW" in balances
        assert "ETH" not in balances

        btc = balances["BTC"]
        assert btc.asset == "BTC"
        assert btc.free == 0.3
        assert btc.locked == 0.2
        assert btc.total == 0.5


class TestGetTradingFee:
    """Tests for Upbit trading fee queries."""

    @pytest.mark.asyncio
    async def test_get_trading_fee(self, connector: UpbitConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.fetch_trading_fee = AsyncMock(return_value={
            "maker": 0.0025,
            "taker": 0.0025,
        })
        connector._exchange = mock_exchange

        fee = await connector.get_trading_fee("BTC/KRW")
        assert fee.maker_pct == pytest.approx(0.25)
        assert fee.taker_pct == pytest.approx(0.25)


class TestGetOrderStatus:
    """Tests for Upbit order status queries."""

    @pytest.mark.asyncio
    async def test_get_order_status_filled(self, connector: UpbitConnector) -> None:
        mock_exchange = AsyncMock()
        mock_exchange.fetch_order = AsyncMock(return_value={
            "id": "order_abc",
            "status": "closed",
            "side": "buy",
            "type": "limit",
            "amount": 0.01,
            "price": 50000000.0,
        })
        connector._exchange = mock_exchange

        order = await connector.get_order_status("order_abc", "BTC/KRW")
        assert order.id == "order_abc"
        assert order.status == OrderStatus.FILLED
        assert order.side == OrderSide.BUY
        assert order.quantity == 0.01


class TestConnectDisconnect:
    """Tests for Upbit connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_creates_exchange(self, connector: UpbitConnector) -> None:
        with patch("arbot.connectors.upbit.ccxt") as mock_ccxt:
            mock_instance = AsyncMock()
            mock_ccxt.upbit = MagicMock(return_value=mock_instance)

            await connector.connect()

            assert connector.is_connected
            mock_ccxt.upbit.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self, connector: UpbitConnector) -> None:
        mock_exchange = AsyncMock()
        connector._exchange = mock_exchange
        connector._set_state(connector.state.__class__("CONNECTED"))

        await connector.disconnect()

        assert not connector.is_connected
        mock_exchange.close.assert_called_once()
        assert connector._exchange is None
