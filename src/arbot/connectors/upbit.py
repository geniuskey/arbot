"""Upbit exchange connector implementation.

Provides WebSocket streaming for order book and trade data, and REST API
access via ccxt for order management, balance queries, and fee lookups.
Upbit uses a unique subscription protocol and KRW-prefixed symbol format.
"""

import time
import uuid
from datetime import datetime, timezone

import ccxt.async_support as ccxt

from arbot.connectors.base import BaseConnector, ConnectionState
from arbot.connectors.rate_limiter import RateLimiter, RateLimiterFactory
from arbot.connectors.websocket_manager import WebSocketManager
from arbot.logging import get_logger
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

# Upbit WebSocket URL
_WS_URL = "wss://api.upbit.com/websocket/v1"

# Map ccxt order status strings to OrderStatus
_CCXT_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.SUBMITTED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.FAILED,
}


def _to_upbit_symbol(symbol: str) -> str:
    """Convert unified symbol to Upbit WebSocket format.

    Args:
        symbol: Unified symbol (e.g. "BTC/KRW").

    Returns:
        Upbit market code (e.g. "KRW-BTC").
    """
    parts = symbol.split("/")
    if len(parts) == 2:
        base, quote = parts
        return f"{quote.upper()}-{base.upper()}"
    return symbol.upper()


def _to_unified_symbol(upbit_symbol: str) -> str:
    """Convert Upbit market code to unified symbol format.

    Args:
        upbit_symbol: Upbit market code (e.g. "KRW-BTC").

    Returns:
        Unified symbol (e.g. "BTC/KRW").
    """
    parts = upbit_symbol.split("-")
    if len(parts) == 2:
        quote, base = parts
        return f"{base.upper()}/{quote.upper()}"
    return upbit_symbol.upper()


def _map_order_type(order_type: OrderType) -> str:
    """Map OrderType enum to ccxt order type string.

    Args:
        order_type: The order type.

    Returns:
        ccxt-compatible order type string.
    """
    match order_type:
        case OrderType.LIMIT:
            return "limit"
        case OrderType.MARKET:
            return "market"
        case OrderType.IOC:
            return "limit"


class UpbitConnector(BaseConnector):
    """Upbit exchange connector with WebSocket streaming and REST API.

    Uses WebSocketManager for real-time order book and trade data, ccxt for
    REST API operations, and RateLimiter for request throttling.

    Upbit-specific behaviors:
    - KRW market with "KRW-BTC" symbol format
    - Subscription via JSON array: [ticket, type_filter, format]
    - Count-based rate limit: 10 requests/second

    Args:
        config: Exchange configuration for Upbit.
        api_key: Upbit API key (optional for public data only).
        api_secret: Upbit API secret (optional for public data only).
    """

    def __init__(
        self,
        config: ExchangeInfo,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        super().__init__("upbit", config)

        self._api_key = api_key
        self._api_secret = api_secret

        # WebSocket manager (created on connect)
        self._ws_manager: WebSocketManager | None = None

        # Rate limiter (count-based, 10/1s)
        self._rate_limiter: RateLimiter = RateLimiterFactory.create("upbit")

        # ccxt exchange instance (created on connect)
        self._exchange: ccxt.upbit | None = None

        # Track subscribed symbols
        self._orderbook_symbols: set[str] = set()
        self._trade_symbols: set[str] = set()

        # Unique ticket for this connection
        self._ticket: str = f"arbot-{uuid.uuid4().hex[:8]}"

    async def connect(self) -> None:
        """Establish Upbit REST API connection."""
        self._set_state(ConnectionState.CONNECTING)

        try:
            ccxt_config: dict = {
                "enableRateLimit": False,
            }
            if self._api_key:
                ccxt_config["apiKey"] = self._api_key
            if self._api_secret:
                ccxt_config["secret"] = self._api_secret

            self._exchange = ccxt.upbit(ccxt_config)

            self._set_state(ConnectionState.CONNECTED)
            self._logger.info("upbit_connected")

        except Exception as e:
            self._set_state(ConnectionState.ERROR)
            self._logger.error("upbit_connect_failed", error=str(e))
            raise ConnectionError(f"Failed to connect to Upbit: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from Upbit WebSocket and REST API."""
        if self._ws_manager is not None:
            await self._ws_manager.disconnect()
            self._ws_manager = None

        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

        self._set_state(ConnectionState.DISCONNECTED)
        self._logger.info("upbit_disconnected")

    async def subscribe_orderbook(self, symbols: list[str], depth: int = 10) -> None:
        """Subscribe to Upbit order book streams.

        Args:
            symbols: Trading pairs (e.g. ["BTC/KRW", "ETH/KRW"]).
            depth: Number of price levels (not configurable on Upbit, ignored).
        """
        self._orderbook_symbols.update(symbols)
        await self._ensure_ws_connected()
        await self._send_subscription()
        self._logger.info("upbit_orderbook_subscribed", symbols=symbols)

    async def subscribe_trades(self, symbols: list[str]) -> None:
        """Subscribe to Upbit trade streams.

        Args:
            symbols: Trading pairs (e.g. ["BTC/KRW"]).
        """
        self._trade_symbols.update(symbols)
        await self._ensure_ws_connected()
        await self._send_subscription()
        self._logger.info("upbit_trades_subscribed", symbols=symbols)

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        """Place an order on Upbit via ccxt.

        Args:
            symbol: Trading pair (e.g. "BTC/KRW").
            side: Buy or sell.
            order_type: LIMIT, MARKET, or IOC.
            quantity: Order quantity in base asset.
            price: Limit price (required for LIMIT/IOC, ignored for MARKET).

        Returns:
            The created Order with Upbit-assigned ID.

        Raises:
            ConnectionError: If not connected.
            ValueError: If price is missing for LIMIT/IOC orders.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Upbit")

        if order_type in (OrderType.LIMIT, OrderType.IOC) and price is None:
            raise ValueError(f"Price is required for {order_type.value} orders")

        await self._rate_limiter.acquire()

        start_time = time.monotonic()
        ccxt_type = _map_order_type(order_type)
        params: dict = {}
        if order_type == OrderType.IOC:
            params["timeInForce"] = "IOC"

        try:
            result = await self._exchange.create_order(
                symbol=symbol,
                type=ccxt_type,
                side=side.value.lower(),
                amount=quantity,
                price=price,
                params=params,
            )
            latency = (time.monotonic() - start_time) * 1000

            status = _CCXT_STATUS_MAP.get(result.get("status", ""), OrderStatus.SUBMITTED)

            order = Order(
                id=str(result.get("id", "")),
                exchange="upbit",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=status,
            )

            self._logger.info(
                "upbit_order_placed",
                order_id=order.id,
                symbol=symbol,
                side=side.value,
                type=order_type.value,
                quantity=quantity,
                price=price,
                latency_ms=round(latency, 2),
            )
            return order

        except ccxt.BaseError as e:
            self._logger.error(
                "upbit_order_failed",
                symbol=symbol,
                side=side.value,
                error=str(e),
            )
            return Order(
                exchange="upbit",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.FAILED,
            )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order on Upbit.

        Args:
            order_id: Upbit order ID (UUID format).
            symbol: Trading pair the order belongs to.

        Returns:
            True if successfully cancelled.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Upbit")

        await self._rate_limiter.acquire()

        try:
            await self._exchange.cancel_order(order_id, symbol)
            self._logger.info("upbit_order_cancelled", order_id=order_id, symbol=symbol)
            return True
        except ccxt.BaseError as e:
            self._logger.error(
                "upbit_cancel_failed",
                order_id=order_id,
                symbol=symbol,
                error=str(e),
            )
            return False

    async def get_order_status(self, order_id: str, symbol: str) -> Order:
        """Query the status of an order on Upbit.

        Args:
            order_id: Upbit order ID.
            symbol: Trading pair.

        Returns:
            Order with current status.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Upbit")

        await self._rate_limiter.acquire()

        result = await self._exchange.fetch_order(order_id, symbol)

        status = _CCXT_STATUS_MAP.get(result.get("status", ""), OrderStatus.SUBMITTED)

        side_str = result.get("side", "buy").upper()
        side = OrderSide.BUY if side_str == "BUY" else OrderSide.SELL

        type_str = result.get("type", "limit").upper()
        order_type = OrderType.LIMIT
        if type_str == "MARKET":
            order_type = OrderType.MARKET

        return Order(
            id=str(result.get("id", order_id)),
            exchange="upbit",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=result.get("amount", 0.0),
            price=result.get("price"),
            status=status,
        )

    async def get_balances(self) -> dict[str, AssetBalance]:
        """Query Upbit account balances.

        Returns:
            Mapping of asset symbol to AssetBalance.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Upbit")

        await self._rate_limiter.acquire()

        balance = await self._exchange.fetch_balance()
        result: dict[str, AssetBalance] = {}

        for asset, info in balance.get("total", {}).items():
            total = float(info) if info else 0.0
            if total > 0:
                free = float(balance.get("free", {}).get(asset, 0.0))
                used = float(balance.get("used", {}).get(asset, 0.0))
                result[asset] = AssetBalance(
                    asset=asset,
                    free=free,
                    locked=used,
                )

        self._logger.info("upbit_balances_fetched", asset_count=len(result))
        return result

    async def get_trading_fee(self, symbol: str) -> TradingFee:
        """Query Upbit trading fee for a symbol.

        Upbit has a fixed fee structure. Falls back to config fees.

        Args:
            symbol: Trading pair.

        Returns:
            TradingFee with maker and taker rates.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Upbit")

        await self._rate_limiter.acquire()

        try:
            fees = await self._exchange.fetch_trading_fee(symbol)
            return TradingFee(
                maker_pct=float(fees.get("maker", 0.0025)) * 100,
                taker_pct=float(fees.get("taker", 0.0025)) * 100,
            )
        except ccxt.BaseError:
            self._logger.warning(
                "upbit_fee_fetch_failed_using_config",
                symbol=symbol,
            )
            return self.config.fees

    async def get_withdrawal_fee(self, asset: str, network: str) -> float:
        """Query Upbit withdrawal fee for an asset.

        Args:
            asset: Asset symbol (e.g. "BTC").
            network: Network name (e.g. "BTC", "ETH").

        Returns:
            Withdrawal fee in the asset's unit.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Upbit")

        await self._rate_limiter.acquire()

        try:
            fees = await self._exchange.fetch_deposit_withdraw_fee(asset)
            networks = fees.get("networks", {})
            if network in networks:
                net_info = networks[network]
                withdraw_fee = net_info.get("fee")
                if withdraw_fee is not None:
                    return float(withdraw_fee)

            default_fee = fees.get("withdraw", {}).get("fee")
            if default_fee is not None:
                return float(default_fee)

            self._logger.warning(
                "upbit_withdrawal_fee_not_found",
                asset=asset,
                network=network,
            )
            return 0.0

        except ccxt.BaseError as e:
            self._logger.error(
                "upbit_withdrawal_fee_failed",
                asset=asset,
                network=network,
                error=str(e),
            )
            return 0.0

    # --- Internal WebSocket Methods ---

    async def _ensure_ws_connected(self) -> None:
        """Create and connect the WebSocket manager if not already active."""
        if self._ws_manager is not None and self._ws_manager.is_connected:
            return

        self._ws_manager = WebSocketManager(
            url=_WS_URL,
            on_message=self._handle_ws_message,
            reconnect_delay=1.0,
            max_reconnect_delay=60.0,
            heartbeat_interval=30.0,
        )
        await self._ws_manager.connect()
        self._logger.info("upbit_ws_connected")

    async def _send_subscription(self) -> None:
        """Send Upbit-format subscription message.

        Upbit expects a JSON array:
        [
            {"ticket": "unique-ticket"},
            {"type": "orderbook", "codes": ["KRW-BTC", ...]},
            {"type": "trade", "codes": ["KRW-BTC", ...]},
            {"format": "DEFAULT"}
        ]
        """
        if self._ws_manager is None or not self._ws_manager.is_connected:
            return

        subscription: list[dict] = [{"ticket": self._ticket}]

        if self._orderbook_symbols:
            codes = [_to_upbit_symbol(s) for s in self._orderbook_symbols]
            subscription.append({"type": "orderbook", "codes": codes})

        if self._trade_symbols:
            codes = [_to_upbit_symbol(s) for s in self._trade_symbols]
            subscription.append({"type": "trade", "codes": codes})

        subscription.append({"format": "DEFAULT"})

        await self._ws_manager.send(subscription)
        self._logger.debug("upbit_subscription_sent", subscription=subscription)

    def _build_subscription_message(self) -> list[dict]:
        """Build the Upbit subscription message array.

        Returns:
            List of subscription dicts in Upbit format.
        """
        subscription: list[dict] = [{"ticket": self._ticket}]

        if self._orderbook_symbols:
            codes = [_to_upbit_symbol(s) for s in self._orderbook_symbols]
            subscription.append({"type": "orderbook", "codes": codes})

        if self._trade_symbols:
            codes = [_to_upbit_symbol(s) for s in self._trade_symbols]
            subscription.append({"type": "trade", "codes": codes})

        subscription.append({"format": "DEFAULT"})
        return subscription

    async def _handle_ws_message(self, data: dict | str) -> None:
        """Route incoming WebSocket messages to the appropriate handler.

        Args:
            data: Parsed message from the WebSocket.
        """
        if not isinstance(data, dict):
            return

        msg_type = data.get("type", "")

        if msg_type == "orderbook":
            await self._handle_orderbook(data)
        elif msg_type == "trade":
            await self._handle_trade(data)

    async def _handle_orderbook(self, data: dict) -> None:
        """Handle an Upbit orderbook message.

        Upbit orderbook format:
        {
            "type": "orderbook",
            "code": "KRW-BTC",
            "timestamp": 1700000000000,
            "orderbook_units": [
                {"ask_price": 50001.0, "bid_price": 50000.0,
                 "ask_size": 1.0, "bid_size": 2.0},
                ...
            ]
        }

        Args:
            data: Orderbook message payload.
        """
        market_code = data.get("code", "")
        symbol = _to_unified_symbol(market_code)
        timestamp = float(data.get("timestamp", time.time() * 1000)) / 1000.0

        units = data.get("orderbook_units", [])

        bids: list[OrderBookEntry] = []
        asks: list[OrderBookEntry] = []

        for unit in units:
            bid_price = float(unit.get("bid_price", 0))
            bid_size = float(unit.get("bid_size", 0))
            ask_price = float(unit.get("ask_price", 0))
            ask_size = float(unit.get("ask_size", 0))

            if bid_size > 0:
                bids.append(OrderBookEntry(price=bid_price, quantity=bid_size))
            if ask_size > 0:
                asks.append(OrderBookEntry(price=ask_price, quantity=ask_size))

        # Upbit units are already sorted, but ensure correctness
        bids.sort(key=lambda e: e.price, reverse=True)
        asks.sort(key=lambda e: e.price)

        orderbook = OrderBook(
            exchange="upbit",
            symbol=symbol,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
        )

        await self._notify_orderbook(orderbook)

    async def _handle_trade(self, data: dict) -> None:
        """Handle an Upbit trade message.

        Upbit trade format:
        {
            "type": "trade",
            "code": "KRW-BTC",
            "trade_price": 50000.0,
            "trade_volume": 0.5,
            "ask_bid": "ASK" or "BID",
            "trade_timestamp": 1700000000000,
            "sequential_id": 123456
        }

        Args:
            data: Trade message payload.
        """
        market_code = data.get("code", "")
        symbol = _to_unified_symbol(market_code)
        price = float(data.get("trade_price", 0))
        quantity = float(data.get("trade_volume", 0))
        ask_bid = data.get("ask_bid", "").upper()
        trade_time = float(data.get("trade_timestamp", time.time() * 1000)) / 1000.0

        # Upbit "ASK" means seller initiated (taker sells) = SELL
        # Upbit "BID" means buyer initiated (taker buys) = BUY
        side = OrderSide.SELL if ask_bid == "ASK" else OrderSide.BUY

        order = Order(
            id=str(data.get("sequential_id", "")),
            exchange="upbit",
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=price,
            status=OrderStatus.FILLED,
        )

        trade_result = TradeResult(
            order=order,
            filled_quantity=quantity,
            filled_price=price,
            fee=0.0,
            fee_asset="",
            latency_ms=0.0,
            filled_at=datetime.fromtimestamp(trade_time, tz=timezone.utc),
        )

        await self._notify_trade(trade_result)
