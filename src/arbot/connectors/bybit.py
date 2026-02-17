"""Bybit exchange connector implementation.

Provides WebSocket streaming for order book and trade data, and REST API
access via ccxt for order management, balance queries, and fee lookups.
"""

import time
from datetime import UTC, datetime

import ccxt.async_support as ccxt

from arbot.connectors.base import BaseConnector, ConnectionState
from arbot.connectors.rate_limiter import RateLimiter, RateLimiterFactory
from arbot.connectors.websocket_manager import WebSocketManager
from arbot.models import (
    AssetBalance,
    ExchangeInfo,
    Order,
    OrderBook,
    OrderBookEntry,
    OrderSide,
    OrderStatus,
    OrderType,
    TradeResult,
    TradingFee,
)

# Bybit WebSocket URL for spot public data (v5 API)
_WS_URL = "wss://stream.bybit.com/v5/public/spot"

# Map ccxt order status strings to OrderStatus
_CCXT_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.SUBMITTED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.FAILED,
}


def _to_bybit_symbol(symbol: str) -> str:
    """Convert unified symbol to Bybit WebSocket format.

    Args:
        symbol: Unified symbol (e.g. "BTC/USDT").

    Returns:
        Bybit stream symbol (e.g. "BTCUSDT").
    """
    return symbol.replace("/", "").upper()


def _to_unified_symbol(bybit_symbol: str) -> str:
    """Convert Bybit stream symbol back to unified format.

    Attempts to split a Bybit symbol (e.g. "BTCUSDT") into unified format
    (e.g. "BTC/USDT") by matching known quote assets.

    Args:
        bybit_symbol: Bybit symbol (e.g. "BTCUSDT").

    Returns:
        Unified symbol (e.g. "BTC/USDT").
    """
    s = bybit_symbol.upper()
    for quote in ("USDT", "USDC", "BTC", "ETH", "DAI"):
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


class BybitConnector(BaseConnector):
    """Bybit exchange connector with WebSocket streaming and REST API.

    Uses WebSocketManager for real-time order book and trade data, ccxt for
    REST API operations, and RateLimiter for request throttling.

    Args:
        config: Exchange configuration for Bybit.
        api_key: Bybit API key (optional for public data only).
        api_secret: Bybit API secret (optional for public data only).
    """

    def __init__(
        self,
        config: ExchangeInfo,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        super().__init__("bybit", config)

        self._api_key = api_key
        self._api_secret = api_secret

        # WebSocket manager (created on connect)
        self._ws_manager: WebSocketManager | None = None

        # Rate limiter (count-based, 600/5s)
        self._rate_limiter: RateLimiter = RateLimiterFactory.create("bybit")

        # ccxt exchange instance (created on connect)
        self._exchange: ccxt.bybit | None = None

        # Track subscribed channels for reconnection
        self._orderbook_symbols: dict[str, int] = {}  # symbol -> depth
        self._trade_symbols: set[str] = set()
        self._subscribed_args: list[str] = []

    async def connect(self) -> None:
        """Establish Bybit REST API and WebSocket connections."""
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

            self._exchange = ccxt.bybit(ccxt_config)

            self._set_state(ConnectionState.CONNECTED)
            self._logger.info("bybit_connected")

        except Exception as e:
            self._set_state(ConnectionState.ERROR)
            self._logger.error("bybit_connect_failed", error=str(e))
            raise ConnectionError(f"Failed to connect to Bybit: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from Bybit WebSocket and REST API."""
        if self._ws_manager is not None:
            await self._ws_manager.disconnect()
            self._ws_manager = None

        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

        self._subscribed_args.clear()
        self._set_state(ConnectionState.DISCONNECTED)
        self._logger.info("bybit_disconnected")

    async def subscribe_orderbook(self, symbols: list[str], depth: int = 10) -> None:
        """Subscribe to Bybit order book depth streams.

        Args:
            symbols: Trading pairs (e.g. ["BTC/USDT", "ETH/USDT"]).
            depth: Number of price levels. Bybit v5 spot supports 1, 50, 200.
                   Other values are mapped to the nearest valid depth.
        """
        # Bybit v5 spot only supports depths: 1, 50, 200
        # Use 50 as minimum useful depth for arbitrage detection
        if depth <= 1:
            bybit_depth = 1
        elif depth <= 50:
            bybit_depth = 50
        else:
            bybit_depth = 200

        for symbol in symbols:
            self._orderbook_symbols[symbol] = bybit_depth

        await self._ensure_ws_connected()

        args = [f"orderbook.{bybit_depth}.{_to_bybit_symbol(s)}" for s in symbols]
        await self._bybit_subscribe(args)
        self._logger.info(
            "bybit_orderbook_subscribed",
            symbols=symbols,
            depth=depth,
        )

    async def subscribe_trades(self, symbols: list[str]) -> None:
        """Subscribe to Bybit trade streams.

        Args:
            symbols: Trading pairs (e.g. ["BTC/USDT"]).
        """
        self._trade_symbols.update(symbols)

        await self._ensure_ws_connected()

        args = [f"publicTrade.{_to_bybit_symbol(s)}" for s in symbols]
        await self._bybit_subscribe(args)
        self._logger.info("bybit_trades_subscribed", symbols=symbols)

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        """Place an order on Bybit via ccxt.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT").
            side: Buy or sell.
            order_type: LIMIT, MARKET, or IOC.
            quantity: Order quantity in base asset.
            price: Limit price (required for LIMIT/IOC, ignored for MARKET).

        Returns:
            The created Order with Bybit-assigned ID.

        Raises:
            ConnectionError: If not connected.
            ValueError: If price is missing for LIMIT/IOC orders.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Bybit")

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
                exchange="bybit",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=status,
            )

            self._logger.info(
                "bybit_order_placed",
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
                "bybit_order_failed",
                symbol=symbol,
                side=side.value,
                error=str(e),
            )
            return Order(
                exchange="bybit",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.FAILED,
            )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order on Bybit.

        Args:
            order_id: Bybit order ID.
            symbol: Trading pair the order belongs to.

        Returns:
            True if successfully cancelled.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Bybit")

        await self._rate_limiter.acquire(weight=1)

        try:
            await self._exchange.cancel_order(order_id, symbol)
            self._logger.info("bybit_order_cancelled", order_id=order_id, symbol=symbol)
            return True
        except ccxt.BaseError as e:
            self._logger.error(
                "bybit_cancel_failed",
                order_id=order_id,
                symbol=symbol,
                error=str(e),
            )
            return False

    async def get_order_status(self, order_id: str, symbol: str) -> Order:
        """Query the status of an order on Bybit.

        Args:
            order_id: Bybit order ID.
            symbol: Trading pair.

        Returns:
            Order with current status.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Bybit")

        await self._rate_limiter.acquire(weight=1)

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
            exchange="bybit",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=result.get("amount", 0.0),
            price=result.get("price"),
            status=status,
        )

    async def get_balances(self) -> dict[str, AssetBalance]:
        """Query Bybit account balances.

        Returns:
            Mapping of asset symbol to AssetBalance.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Bybit")

        await self._rate_limiter.acquire(weight=1)

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

        self._logger.info("bybit_balances_fetched", asset_count=len(result))
        return result

    async def get_trading_fee(self, symbol: str) -> TradingFee:
        """Query Bybit trading fee for a symbol.

        Falls back to config fees if the API call fails.

        Args:
            symbol: Trading pair.

        Returns:
            TradingFee with maker and taker rates.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Bybit")

        await self._rate_limiter.acquire(weight=1)

        try:
            fees = await self._exchange.fetch_trading_fee(symbol)
            return TradingFee(
                maker_pct=float(fees.get("maker", 0.001)) * 100,
                taker_pct=float(fees.get("taker", 0.001)) * 100,
            )
        except ccxt.BaseError:
            self._logger.warning(
                "bybit_fee_fetch_failed_using_config",
                symbol=symbol,
            )
            return self.config.fees

    async def get_withdrawal_fee(self, asset: str, network: str) -> float:
        """Query Bybit withdrawal fee for an asset on a network.

        Args:
            asset: Asset symbol (e.g. "USDT").
            network: Network name (e.g. "TRC20", "ERC20").

        Returns:
            Withdrawal fee in the asset's unit.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to Bybit")

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
                "bybit_withdrawal_fee_not_found",
                asset=asset,
                network=network,
            )
            return 0.0

        except ccxt.BaseError as e:
            self._logger.error(
                "bybit_withdrawal_fee_failed",
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
            heartbeat_interval=20.0,
        )
        await self._ws_manager.connect()
        self._logger.info("bybit_ws_connected")

        # Re-subscribe previously tracked channels after reconnection
        if self._subscribed_args:
            await self._bybit_subscribe(list(self._subscribed_args))

    async def _bybit_subscribe(self, args: list[str]) -> None:
        """Send a Bybit-format subscribe message.

        Bybit uses {"op": "subscribe", "args": [...]} instead of the
        Binance-style {"method": "SUBSCRIBE", "params": [...]}, so we
        send directly via ws_manager.send() rather than ws_manager.subscribe().

        Args:
            args: List of Bybit subscription topics (e.g. ["orderbook.10.BTCUSDT"]).
        """
        # Track for reconnection
        for arg in args:
            if arg not in self._subscribed_args:
                self._subscribed_args.append(arg)

        if self._ws_manager is not None and self._ws_manager.is_connected:
            subscribe_msg = {
                "op": "subscribe",
                "args": args,
            }
            await self._ws_manager.send(subscribe_msg)
            self._logger.debug("bybit_ws_subscribed", args=args)

    async def _handle_ws_message(self, data: dict | str) -> None:
        """Route incoming WebSocket messages to the appropriate handler.

        Bybit messages contain a "topic" field that identifies the data type,
        unlike Binance's combined stream format.

        Args:
            data: Parsed message from the WebSocket.
        """
        if not isinstance(data, dict):
            return

        # Handle subscription confirmations and pong responses
        op = data.get("op")
        if op in ("subscribe", "pong"):
            success = data.get("success", False)
            if not success:
                self._logger.warning("bybit_ws_op_failed", op=op, data=data)
            return

        topic = data.get("topic", "")
        if not topic:
            return

        if topic.startswith("orderbook."):
            await self._handle_orderbook(data, topic)
        elif topic.startswith("publicTrade."):
            await self._handle_trade(data)

    async def _handle_orderbook(self, data: dict, topic: str) -> None:
        """Handle a Bybit order book message (snapshot or delta).

        Args:
            data: Full message with topic, type, ts, and data fields.
            topic: Topic string (e.g. "orderbook.10.BTCUSDT").
        """
        payload = data.get("data", {})
        if not isinstance(payload, dict):
            return

        raw_symbol = payload.get("s", "")
        symbol = _to_unified_symbol(raw_symbol)
        timestamp = float(data.get("ts", time.time() * 1000)) / 1000.0

        bids = [
            OrderBookEntry(price=float(b[0]), quantity=float(b[1]))
            for b in payload.get("b", [])
            if float(b[1]) > 0
        ]
        asks = [
            OrderBookEntry(price=float(a[0]), quantity=float(a[1]))
            for a in payload.get("a", [])
            if float(a[1]) > 0
        ]

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda e: e.price, reverse=True)
        asks.sort(key=lambda e: e.price)

        orderbook = OrderBook(
            exchange="bybit",
            symbol=symbol,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
        )

        await self._notify_orderbook(orderbook)

    async def _handle_trade(self, data: dict) -> None:
        """Handle a Bybit public trade message.

        Bybit sends trades as an array in data. Each trade has:
        - T: trade timestamp (ms)
        - s: symbol
        - S: side ("Buy" or "Sell")
        - v: quantity
        - p: price
        - i: trade ID

        Args:
            data: Full message with topic, type, ts, and data fields.
        """
        trades = data.get("data", [])
        if not isinstance(trades, list):
            return

        for trade in trades:
            if not isinstance(trade, dict):
                continue

            raw_symbol = trade.get("s", "")
            symbol = _to_unified_symbol(raw_symbol)
            price = float(trade.get("p", 0))
            quantity = float(trade.get("v", 0))
            trade_time = float(trade.get("T", time.time() * 1000)) / 1000.0

            side_str = trade.get("S", "Buy")
            side = OrderSide.BUY if side_str == "Buy" else OrderSide.SELL

            order = Order(
                id=str(trade.get("i", "")),
                exchange="bybit",
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
                filled_at=datetime.fromtimestamp(trade_time, tz=UTC),
            )

            await self._notify_trade(trade_result)
