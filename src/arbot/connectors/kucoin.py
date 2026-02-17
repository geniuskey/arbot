"""KuCoin exchange connector implementation.

Provides WebSocket streaming for order book and trade data, and REST API
access via ccxt for order management, balance queries, and fee lookups.

KuCoin WebSocket requires a 2-step connection: first obtain a token via
the bullet-public REST endpoint, then connect to the WS endpoint with
that token.
"""

import asyncio
import time
from datetime import UTC, datetime

import aiohttp
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

# KuCoin bullet-public endpoint for obtaining WS tokens
_BULLET_PUBLIC_URL = "https://api.kucoin.com/api/v1/bullet-public"

# Default ping interval if not provided by server (ms)
_DEFAULT_PING_INTERVAL_MS = 18000

# Map ccxt order status strings to OrderStatus
_CCXT_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.SUBMITTED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.FAILED,
}


def _to_kucoin_symbol(symbol: str) -> str:
    """Convert unified symbol to KuCoin WebSocket format.

    Args:
        symbol: Unified symbol (e.g. "BTC/USDT").

    Returns:
        KuCoin stream symbol (e.g. "BTC-USDT").
    """
    return symbol.replace("/", "-").upper()


def _to_unified_symbol(kucoin_symbol: str) -> str:
    """Convert KuCoin stream symbol back to unified format.

    Args:
        kucoin_symbol: KuCoin symbol (e.g. "BTC-USDT").

    Returns:
        Unified symbol (e.g. "BTC/USDT").
    """
    return kucoin_symbol.replace("-", "/").upper()


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


class KuCoinConnector(BaseConnector):
    """KuCoin exchange connector with WebSocket streaming and REST API.

    Uses WebSocketManager for real-time order book and trade data, ccxt for
    REST API operations, and RateLimiter for request throttling.

    KuCoin requires a 2-step WebSocket connection: first a REST call to
    bullet-public for a token, then connecting to the provided endpoint.

    Args:
        config: Exchange configuration for KuCoin.
        api_key: KuCoin API key (optional for public data only).
        api_secret: KuCoin API secret (optional for public data only).
        api_passphrase: KuCoin API passphrase (optional for public data only).
    """

    def __init__(
        self,
        config: ExchangeInfo,
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
    ) -> None:
        super().__init__("kucoin", config)

        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase

        # WebSocket manager (created on connect)
        self._ws_manager: WebSocketManager | None = None

        # Rate limiter (count-based, 100/10s)
        self._rate_limiter: RateLimiter = RateLimiterFactory.create("kucoin")

        # ccxt exchange instance (created on connect)
        self._exchange: ccxt.kucoin | None = None

        # Track subscribed channels for reconnection
        self._orderbook_symbols: dict[str, int] = {}  # symbol -> depth
        self._trade_symbols: set[str] = set()
        self._subscribed_topics: list[str] = []
        self._ping_task: asyncio.Task | None = None

        # KuCoin WS token and endpoint (from bullet-public)
        self._ws_token: str = ""
        self._ws_endpoint: str = ""
        self._ping_interval_ms: int = _DEFAULT_PING_INTERVAL_MS

    async def connect(self) -> None:
        """Establish KuCoin REST API and WebSocket connections."""
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
            if self._api_passphrase:
                ccxt_config["password"] = self._api_passphrase

            self._exchange = ccxt.kucoin(ccxt_config)

            self._set_state(ConnectionState.CONNECTED)
            self._logger.info("kucoin_connected")

        except Exception as e:
            self._set_state(ConnectionState.ERROR)
            self._logger.error("kucoin_connect_failed", error=str(e))
            raise ConnectionError(f"Failed to connect to KuCoin: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from KuCoin WebSocket and REST API."""
        if self._ping_task is not None:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

        if self._ws_manager is not None:
            await self._ws_manager.disconnect()
            self._ws_manager = None

        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

        self._subscribed_topics.clear()
        self._ws_token = ""
        self._ws_endpoint = ""
        self._set_state(ConnectionState.DISCONNECTED)
        self._logger.info("kucoin_disconnected")

    async def subscribe_orderbook(self, symbols: list[str], depth: int = 10) -> None:
        """Subscribe to KuCoin order book depth streams.

        Uses level2Depth50 which provides full 50-level snapshots
        (no delta management needed).

        Args:
            symbols: Trading pairs (e.g. ["BTC/USDT", "ETH/USDT"]).
            depth: Number of price levels. KuCoin provides 5 or 50 levels.
                   Values <= 5 use 5-level stream, otherwise 50-level.
        """
        # KuCoin supports level2Depth5 and level2Depth50
        kucoin_depth = 5 if depth <= 5 else 50

        for symbol in symbols:
            self._orderbook_symbols[symbol] = kucoin_depth

        await self._ensure_ws_connected()

        # Subscribe per-symbol to avoid one invalid symbol failing the batch
        for symbol in symbols:
            topic = f"/spotMarket/level2Depth{kucoin_depth}:{_to_kucoin_symbol(symbol)}"
            await self._kucoin_subscribe(topic)
        self._logger.info(
            "kucoin_orderbook_subscribed",
            symbols=symbols,
            depth=kucoin_depth,
        )

    async def subscribe_trades(self, symbols: list[str]) -> None:
        """Subscribe to KuCoin trade streams.

        Args:
            symbols: Trading pairs (e.g. ["BTC/USDT"]).
        """
        self._trade_symbols.update(symbols)

        await self._ensure_ws_connected()

        for symbol in symbols:
            topic = f"/market/match:{_to_kucoin_symbol(symbol)}"
            await self._kucoin_subscribe(topic)
        self._logger.info("kucoin_trades_subscribed", symbols=symbols)

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        """Place an order on KuCoin via ccxt.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT").
            side: Buy or sell.
            order_type: LIMIT, MARKET, or IOC.
            quantity: Order quantity in base asset.
            price: Limit price (required for LIMIT/IOC, ignored for MARKET).

        Returns:
            The created Order with KuCoin-assigned ID.

        Raises:
            ConnectionError: If not connected.
            ValueError: If price is missing for LIMIT/IOC orders.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to KuCoin")

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
                exchange="kucoin",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=status,
            )

            self._logger.info(
                "kucoin_order_placed",
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
                "kucoin_order_failed",
                symbol=symbol,
                side=side.value,
                error=str(e),
            )
            return Order(
                exchange="kucoin",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.FAILED,
            )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order on KuCoin.

        Args:
            order_id: KuCoin order ID.
            symbol: Trading pair the order belongs to.

        Returns:
            True if successfully cancelled.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to KuCoin")

        await self._rate_limiter.acquire(weight=1)

        try:
            await self._exchange.cancel_order(order_id, symbol)
            self._logger.info("kucoin_order_cancelled", order_id=order_id, symbol=symbol)
            return True
        except ccxt.BaseError as e:
            self._logger.error(
                "kucoin_cancel_failed",
                order_id=order_id,
                symbol=symbol,
                error=str(e),
            )
            return False

    async def get_order_status(self, order_id: str, symbol: str) -> Order:
        """Query the status of an order on KuCoin.

        Args:
            order_id: KuCoin order ID.
            symbol: Trading pair.

        Returns:
            Order with current status.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to KuCoin")

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
            exchange="kucoin",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=result.get("amount", 0.0),
            price=result.get("price"),
            status=status,
        )

    async def get_balances(self) -> dict[str, AssetBalance]:
        """Query KuCoin account balances.

        Returns:
            Mapping of asset symbol to AssetBalance.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to KuCoin")

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

        self._logger.info("kucoin_balances_fetched", asset_count=len(result))
        return result

    async def get_trading_fee(self, symbol: str) -> TradingFee:
        """Query KuCoin trading fee for a symbol.

        Falls back to config fees if the API call fails.

        Args:
            symbol: Trading pair.

        Returns:
            TradingFee with maker and taker rates.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to KuCoin")

        await self._rate_limiter.acquire(weight=1)

        try:
            fees = await self._exchange.fetch_trading_fee(symbol)
            return TradingFee(
                maker_pct=float(fees.get("maker", 0.001)) * 100,
                taker_pct=float(fees.get("taker", 0.001)) * 100,
            )
        except ccxt.BaseError:
            self._logger.warning(
                "kucoin_fee_fetch_failed_using_config",
                symbol=symbol,
            )
            return self.config.fees

    async def get_withdrawal_fee(self, asset: str, network: str) -> float:
        """Query KuCoin withdrawal fee for an asset on a network.

        Args:
            asset: Asset symbol (e.g. "USDT").
            network: Network name (e.g. "TRC20", "ERC20").

        Returns:
            Withdrawal fee in the asset's unit.
        """
        if self._exchange is None:
            raise ConnectionError("Not connected to KuCoin")

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
                "kucoin_withdrawal_fee_not_found",
                asset=asset,
                network=network,
            )
            return 0.0

        except ccxt.BaseError as e:
            self._logger.error(
                "kucoin_withdrawal_fee_failed",
                asset=asset,
                network=network,
                error=str(e),
            )
            return 0.0

    # --- Internal WebSocket Methods ---

    async def _fetch_ws_token(self) -> None:
        """Fetch a WebSocket token from KuCoin's bullet-public endpoint.

        Populates _ws_token, _ws_endpoint, and _ping_interval_ms from the
        server response.

        Raises:
            ConnectionError: If the token request fails.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(_BULLET_PUBLIC_URL) as resp:
                    if resp.status != 200:
                        raise ConnectionError(
                            f"KuCoin bullet-public returned status {resp.status}"
                        )
                    body = await resp.json()

            data = body.get("data", {})
            self._ws_token = data.get("token", "")
            if not self._ws_token:
                raise ConnectionError("KuCoin bullet-public returned empty token")

            servers = data.get("instanceServers", [])
            if servers:
                server = servers[0]
                self._ws_endpoint = server.get("endpoint", "")
                self._ping_interval_ms = server.get("pingInterval", _DEFAULT_PING_INTERVAL_MS)
            else:
                raise ConnectionError("KuCoin bullet-public returned no instanceServers")

            self._logger.info(
                "kucoin_ws_token_fetched",
                endpoint=self._ws_endpoint,
                ping_interval_ms=self._ping_interval_ms,
            )

        except aiohttp.ClientError as e:
            raise ConnectionError(f"Failed to fetch KuCoin WS token: {e}") from e

    async def _ensure_ws_connected(self) -> None:
        """Create and connect the WebSocket manager if not already active.

        Performs the 2-step KuCoin WS connection:
        1. Fetch a token via bullet-public REST endpoint
        2. Connect to the WS endpoint with that token
        """
        if self._ws_manager is not None and self._ws_manager.is_connected:
            return

        # Step 1: Get WS token and endpoint
        await self._fetch_ws_token()

        # Step 2: Connect with token
        ws_url = f"{self._ws_endpoint}?token={self._ws_token}"

        self._ws_manager = WebSocketManager(
            url=ws_url,
            on_message=self._handle_ws_message,
            reconnect_delay=1.0,
            max_reconnect_delay=60.0,
            heartbeat_interval=0,  # Disable standard WS ping; use KuCoin JSON ping
        )
        await self._ws_manager.connect()
        self._logger.info("kucoin_ws_connected")

        # Start KuCoin-specific JSON ping loop
        if self._ping_task is not None:
            self._ping_task.cancel()
        self._ping_task = asyncio.create_task(self._kucoin_ping_loop())

        # Re-subscribe previously tracked channels after reconnection
        if self._subscribed_topics:
            for topic in list(self._subscribed_topics):
                await self._kucoin_subscribe(topic)

    async def _kucoin_ping_loop(self) -> None:
        """Send KuCoin-specific JSON ping at the server-specified interval.

        KuCoin requires {"type": "ping"} messages and closes connections
        if no ping is received within the pingTimeout window.
        """
        ping_interval_s = self._ping_interval_ms / 1000.0
        # Send slightly more frequently than required to avoid timeout
        interval = max(ping_interval_s * 0.8, 5.0)

        try:
            while self._ws_manager is not None and self._ws_manager.is_connected:
                await asyncio.sleep(interval)
                try:
                    await self._ws_manager.send({"id": "ping", "type": "ping"})
                except (ConnectionError, Exception):
                    self._logger.warning("kucoin_ping_failed")
                    break
        except asyncio.CancelledError:
            pass

    async def _kucoin_subscribe(self, topic: str) -> None:
        """Send a KuCoin-format subscribe message.

        KuCoin uses {"type": "subscribe", "topic": "..."} messages, so we
        send directly via ws_manager.send() rather than ws_manager.subscribe().

        Args:
            topic: KuCoin subscription topic
                (e.g. "/spotMarket/level2Depth50:BTC-USDT").
        """
        # Track for reconnection
        if topic not in self._subscribed_topics:
            self._subscribed_topics.append(topic)

        if self._ws_manager is not None and self._ws_manager.is_connected:
            subscribe_msg = {
                "id": topic,
                "type": "subscribe",
                "topic": topic,
                "privateChannel": False,
                "response": True,
            }
            await self._ws_manager.send(subscribe_msg)
            self._logger.debug("kucoin_ws_subscribed", topic=topic)

    async def _handle_ws_message(self, data: dict | str) -> None:
        """Route incoming WebSocket messages to the appropriate handler.

        KuCoin messages have a "type" field for routing:
        - "welcome": initial connection confirmation
        - "ack": subscription confirmation
        - "pong": heartbeat response
        - "message": actual data message with a "topic" field

        Args:
            data: Parsed message from the WebSocket.
        """
        if not isinstance(data, dict):
            return

        msg_type = data.get("type", "")

        # Handle non-data messages
        if msg_type in ("welcome", "ack", "pong"):
            return

        if msg_type != "message":
            return

        topic = data.get("topic", "")
        if not topic:
            return

        if "/spotMarket/level2Depth" in topic:
            await self._handle_orderbook(data, topic)
        elif "/market/match:" in topic:
            await self._handle_trade(data, topic)

    async def _handle_orderbook(self, data: dict, topic: str) -> None:
        """Handle a KuCoin order book snapshot message.

        KuCoin level2Depth50 provides full snapshots with asks and bids
        as [price, size] string pairs.

        Args:
            data: Full message with topic, type, and data fields.
            topic: Topic string (e.g. "/spotMarket/level2Depth50:BTC-USDT").
        """
        payload = data.get("data", {})
        if not isinstance(payload, dict):
            return

        # Extract symbol from topic: "/spotMarket/level2Depth50:BTC-USDT" -> "BTC-USDT"
        parts = topic.split(":")
        if len(parts) < 2:
            return
        raw_symbol = parts[-1]
        symbol = _to_unified_symbol(raw_symbol)

        timestamp = float(payload.get("timestamp", time.time() * 1000)) / 1000.0

        bids = [
            OrderBookEntry(price=float(b[0]), quantity=float(b[1]))
            for b in payload.get("bids", [])
            if float(b[1]) > 0
        ]
        asks = [
            OrderBookEntry(price=float(a[0]), quantity=float(a[1]))
            for a in payload.get("asks", [])
            if float(a[1]) > 0
        ]

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda e: e.price, reverse=True)
        asks.sort(key=lambda e: e.price)

        orderbook = OrderBook(
            exchange="kucoin",
            symbol=symbol,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
        )

        await self._notify_orderbook(orderbook)

    async def _handle_trade(self, data: dict, topic: str) -> None:
        """Handle a KuCoin trade (match) message.

        KuCoin sends individual trades with fields:
        - symbol: "BTC-USDT"
        - side: "buy" or "sell"
        - price: string price
        - size: string quantity
        - tradeId: trade identifier
        - time: nanosecond timestamp string

        Args:
            data: Full message with topic, type, and data fields.
            topic: Topic string (e.g. "/market/match:BTC-USDT").
        """
        trade = data.get("data", {})
        if not isinstance(trade, dict):
            return

        raw_symbol = trade.get("symbol", "")
        if not raw_symbol:
            # Fallback: extract from topic
            parts = topic.split(":")
            if len(parts) >= 2:
                raw_symbol = parts[-1]
        symbol = _to_unified_symbol(raw_symbol)

        price = float(trade.get("price", 0))
        quantity = float(trade.get("size", 0))

        # KuCoin time is in nanoseconds
        time_ns = int(trade.get("time", 0))
        trade_time = time_ns / 1_000_000_000.0 if time_ns else time.time()

        side_str = trade.get("side", "buy").lower()
        side = OrderSide.BUY if side_str == "buy" else OrderSide.SELL

        order = Order(
            id=str(trade.get("tradeId", "")),
            exchange="kucoin",
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
