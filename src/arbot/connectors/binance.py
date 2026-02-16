"""Binance exchange connector implementation.

Provides WebSocket streaming for order book and trade data, and REST API
access via ccxt for order management, balance queries, and fee lookups.
"""

import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt

from arbot.connectors.base import BaseConnector, ConnectionState
from arbot.connectors.rate_limiter import RateLimiterFactory, RateLimiter
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

# Binance WebSocket base URLs
_WS_BASE_URL = "wss://stream.binance.com:9443/ws"
_WS_COMBINED_URL = "wss://stream.binance.com:9443/stream"

# Map ccxt order status strings to OrderStatus
_CCXT_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.SUBMITTED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.FAILED,
}


def _to_binance_symbol(symbol: str) -> str:
    """Convert unified symbol to Binance WebSocket format.

    Args:
        symbol: Unified symbol (e.g. "BTC/USDT").

    Returns:
        Binance stream symbol (e.g. "btcusdt").
    """
    return symbol.replace("/", "").lower()


def _to_unified_symbol(binance_symbol: str) -> str:
    """Convert Binance stream symbol back to unified format.

    Attempts to split a lowercase Binance symbol (e.g. "btcusdt") into
    unified format (e.g. "BTC/USDT") by matching known quote assets.

    Args:
        binance_symbol: Binance symbol (e.g. "btcusdt").

    Returns:
        Unified symbol (e.g. "BTC/USDT").
    """
    s = binance_symbol.upper()
    for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH", "BNB"):
        if s.endswith(quote):
            base = s[: -len(quote)]
            if base:
                return f"{base}/{quote}"
    return s


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
            return "limit"  # IOC is a limit order with timeInForce=IOC


class BinanceConnector(BaseConnector):
    """Binance exchange connector with WebSocket streaming and REST API.

    Uses WebSocketManager for real-time order book and trade data, ccxt for
    REST API operations, and RateLimiter for request throttling.

    Args:
        config: Exchange configuration for Binance.
        api_key: Binance API key (optional for public data only).
        api_secret: Binance API secret (optional for public data only).
    """

    def __init__(
        self,
        config: ExchangeInfo,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        super().__init__("binance", config)

        self._api_key = api_key
        self._api_secret = api_secret

        # WebSocket manager (created on connect)
        self._ws_manager: WebSocketManager | None = None

        # Rate limiter (weight-based, 1200/min)
        self._rate_limiter: RateLimiter = RateLimiterFactory.create("binance")

        # ccxt exchange instance (created on connect)
        self._exchange: ccxt.binance | None = None

        # Track subscribed symbols for stream URL building
        self._orderbook_symbols: dict[str, int] = {}  # symbol -> depth
        self._trade_symbols: set[str] = set()

    async def connect(self) -> None:
        """Establish Binance REST API and WebSocket connections."""
        self._set_state(ConnectionState.CONNECTING)

        try:
            # Initialize ccxt exchange
            ccxt_config: dict = {
                "enableRateLimit": False,  # We handle rate limiting ourselves
            }
            if self._api_key:
                ccxt_config["apiKey"] = self._api_key
            if self._api_secret:
                ccxt_config["secret"] = self._api_secret

            self._exchange = ccxt.binance(ccxt_config)

            self._set_state(ConnectionState.CONNECTED)
            self._logger.info("binance_connected")

        except Exception as e:
            self._set_state(ConnectionState.ERROR)
            self._logger.error("binance_connect_failed", error=str(e))
            raise ConnectionError(f"Failed to connect to Binance: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from Binance WebSocket and REST API."""
        if self._ws_manager is not None:
            await self._ws_manager.disconnect()
            self._ws_manager = None

        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

        self._set_state(ConnectionState.DISCONNECTED)
        self._logger.info("binance_disconnected")

    async def subscribe_orderbook(self, symbols: list[str], depth: int = 10) -> None:
        """Subscribe to Binance partial book depth streams.

        Args:
            symbols: Trading pairs (e.g. ["BTC/USDT", "ETH/USDT"]).
            depth: Number of price levels (5, 10, or 20).
        """
        for symbol in symbols:
            self._orderbook_symbols[symbol] = depth

        await self._ensure_ws_connected()

        channels = [
            f"{_to_binance_symbol(s)}@depth{depth}@100ms"
            for s in symbols
        ]
        if self._ws_manager is not None:
            await self._ws_manager.subscribe(channels)
            self._logger.info(
                "binance_orderbook_subscribed",
                symbols=symbols,
                depth=depth,
            )

    async def subscribe_trades(self, symbols: list[str]) -> None:
        """Subscribe to Binance trade streams.

        Args:
            symbols: Trading pairs (e.g. ["BTC/USDT"]).
        """
        self._trade_symbols.update(symbols)

        await self._ensure_ws_connected()

        channels = [
            f"{_to_binance_symbol(s)}@trade"
            for s in symbols
        ]
        if self._ws_manager is not None:
            await self._ws_manager.subscribe(channels)
            self._logger.info("binance_trades_subscribed", symbols=symbols)

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        """Place an order on Binance via ccxt.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT").
            side: Buy or sell.
            order_type: LIMIT, MARKET, or IOC.
            quantity: Order quantity in base asset.
            price: Limit price (required for LIMIT/IOC, ignored for MARKET).

        Returns:
            The created Order with Binance-assigned ID.

        Raises:
            ConnectionError: If not connected.
            ValueError: If price is missing for LIMIT/IOC orders.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Binance")

        if order_type in (OrderType.LIMIT, OrderType.IOC) and price is None:
            raise ValueError(f"Price is required for {order_type.value} orders")

        await self._rate_limiter.acquire(weight=1)

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
                exchange="binance",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=status,
            )

            self._logger.info(
                "binance_order_placed",
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
                "binance_order_failed",
                symbol=symbol,
                side=side.value,
                error=str(e),
            )
            return Order(
                exchange="binance",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.FAILED,
            )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order on Binance.

        Args:
            order_id: Binance order ID.
            symbol: Trading pair the order belongs to.

        Returns:
            True if successfully cancelled.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Binance")

        await self._rate_limiter.acquire(weight=1)

        try:
            await self._exchange.cancel_order(order_id, symbol)
            self._logger.info("binance_order_cancelled", order_id=order_id, symbol=symbol)
            return True
        except ccxt.BaseError as e:
            self._logger.error(
                "binance_cancel_failed",
                order_id=order_id,
                symbol=symbol,
                error=str(e),
            )
            return False

    async def get_order_status(self, order_id: str, symbol: str) -> Order:
        """Query the status of an order on Binance.

        Args:
            order_id: Binance order ID.
            symbol: Trading pair.

        Returns:
            Order with current status.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Binance")

        await self._rate_limiter.acquire(weight=2)

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
            exchange="binance",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=result.get("amount", 0.0),
            price=result.get("price"),
            status=status,
        )

    async def get_balances(self) -> dict[str, AssetBalance]:
        """Query Binance account balances.

        Returns:
            Mapping of asset symbol to AssetBalance.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Binance")

        await self._rate_limiter.acquire(weight=10)

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

        self._logger.info("binance_balances_fetched", asset_count=len(result))
        return result

    async def get_trading_fee(self, symbol: str) -> TradingFee:
        """Query Binance trading fee for a symbol.

        Falls back to config fees if the API call fails.

        Args:
            symbol: Trading pair.

        Returns:
            TradingFee with maker and taker rates.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Binance")

        await self._rate_limiter.acquire(weight=1)

        try:
            fees = await self._exchange.fetch_trading_fee(symbol)
            return TradingFee(
                maker_pct=float(fees.get("maker", 0.001)) * 100,
                taker_pct=float(fees.get("taker", 0.001)) * 100,
            )
        except ccxt.BaseError:
            self._logger.warning(
                "binance_fee_fetch_failed_using_config",
                symbol=symbol,
            )
            return self.config.fees

    async def get_withdrawal_fee(self, asset: str, network: str) -> float:
        """Query Binance withdrawal fee for an asset on a network.

        Args:
            asset: Asset symbol (e.g. "USDT").
            network: Network name (e.g. "TRC20", "ERC20").

        Returns:
            Withdrawal fee in the asset's unit.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Binance")

        await self._rate_limiter.acquire(weight=1)

        try:
            fees = await self._exchange.fetch_deposit_withdraw_fee(asset)
            networks = fees.get("networks", {})
            if network in networks:
                net_info = networks[network]
                withdraw_fee = net_info.get("fee")
                if withdraw_fee is not None:
                    return float(withdraw_fee)

            # Fallback to the default fee if network not found
            default_fee = fees.get("withdraw", {}).get("fee")
            if default_fee is not None:
                return float(default_fee)

            self._logger.warning(
                "binance_withdrawal_fee_not_found",
                asset=asset,
                network=network,
            )
            return 0.0

        except ccxt.BaseError as e:
            self._logger.error(
                "binance_withdrawal_fee_failed",
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
            url=_WS_COMBINED_URL,
            on_message=self._handle_ws_message,
            reconnect_delay=1.0,
            max_reconnect_delay=60.0,
            heartbeat_interval=30.0,
        )
        await self._ws_manager.connect()
        self._logger.info("binance_ws_connected")

    async def _handle_ws_message(self, data: dict | str) -> None:
        """Route incoming WebSocket messages to the appropriate handler.

        Args:
            data: Parsed message from the WebSocket.
        """
        if not isinstance(data, dict):
            return

        # Combined stream format wraps data in {"stream": ..., "data": ...}
        if "stream" in data and "data" in data:
            stream = data["stream"]
            payload = data["data"]
        else:
            stream = data.get("e", "")
            payload = data

        if not isinstance(payload, dict):
            return

        event_type = payload.get("e", "")

        if event_type == "depthUpdate":
            await self._handle_depth_update(payload)
        elif event_type == "trade":
            await self._handle_trade_update(payload)
        elif "@depth" in str(stream):
            # Partial book depth snapshots (non-diff format)
            await self._handle_partial_depth(payload, stream)

    async def _handle_depth_update(self, data: dict) -> None:
        """Handle a Binance depth update (diff) message.

        Args:
            data: Depth update payload with 'b' (bids) and 'a' (asks).
        """
        raw_symbol = data.get("s", "")
        symbol = _to_unified_symbol(raw_symbol)
        timestamp = float(data.get("E", time.time() * 1000)) / 1000.0

        bids = [
            OrderBookEntry(price=float(b[0]), quantity=float(b[1]))
            for b in data.get("b", [])
            if float(b[1]) > 0
        ]
        asks = [
            OrderBookEntry(price=float(a[0]), quantity=float(a[1]))
            for a in data.get("a", [])
            if float(a[1]) > 0
        ]

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda e: e.price, reverse=True)
        asks.sort(key=lambda e: e.price)

        orderbook = OrderBook(
            exchange="binance",
            symbol=symbol,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
        )

        await self._notify_orderbook(orderbook)

    async def _handle_partial_depth(self, data: dict, stream: str) -> None:
        """Handle a Binance partial book depth snapshot.

        Args:
            data: Depth snapshot payload with 'bids' and 'asks'.
            stream: Stream name (e.g. "btcusdt@depth10@100ms").
        """
        # Extract symbol from stream name
        parts = stream.split("@")
        raw_symbol = parts[0] if parts else ""
        symbol = _to_unified_symbol(raw_symbol)
        timestamp = float(data.get("lastUpdateId", time.time() * 1000))

        bids = [
            OrderBookEntry(price=float(b[0]), quantity=float(b[1]))
            for b in data.get("bids", [])
            if float(b[1]) > 0
        ]
        asks = [
            OrderBookEntry(price=float(a[0]), quantity=float(a[1]))
            for a in data.get("asks", [])
            if float(a[1]) > 0
        ]

        bids.sort(key=lambda e: e.price, reverse=True)
        asks.sort(key=lambda e: e.price)

        orderbook = OrderBook(
            exchange="binance",
            symbol=symbol,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
        )

        await self._notify_orderbook(orderbook)

    async def _handle_trade_update(self, data: dict) -> None:
        """Handle a Binance trade stream message.

        Args:
            data: Trade event payload.
        """
        raw_symbol = data.get("s", "")
        symbol = _to_unified_symbol(raw_symbol)
        price = float(data.get("p", 0))
        quantity = float(data.get("q", 0))
        is_buyer_maker = data.get("m", False)
        trade_time = float(data.get("T", time.time() * 1000)) / 1000.0

        side = OrderSide.SELL if is_buyer_maker else OrderSide.BUY

        order = Order(
            id=str(data.get("t", "")),
            exchange="binance",
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
