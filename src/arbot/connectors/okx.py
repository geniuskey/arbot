"""OKX exchange connector implementation.

Provides WebSocket streaming for order book data, and REST API access
via ccxt for order management, balance queries, and fee lookups.
"""

import json
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

_WS_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"

_CCXT_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.SUBMITTED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.FAILED,
}


def _to_okx_inst_id(symbol: str) -> str:
    """Convert unified symbol to OKX instId format.

    Args:
        symbol: Unified symbol (e.g. "BTC/USDT").

    Returns:
        OKX instrument ID (e.g. "BTC-USDT").
    """
    return symbol.replace("/", "-")


def _to_unified_symbol(inst_id: str) -> str:
    """Convert OKX instId to unified symbol format.

    Args:
        inst_id: OKX instrument ID (e.g. "BTC-USDT").

    Returns:
        Unified symbol (e.g. "BTC/USDT").
    """
    return inst_id.replace("-", "/")


def _map_order_type(order_type: OrderType) -> str:
    match order_type:
        case OrderType.LIMIT:
            return "limit"
        case OrderType.MARKET:
            return "market"
        case OrderType.IOC:
            return "limit"


class OKXConnector(BaseConnector):
    """OKX exchange connector with WebSocket streaming and REST API.

    Args:
        config: Exchange configuration for OKX.
        api_key: OKX API key (optional for public data).
        api_secret: OKX API secret (optional for public data).
        passphrase: OKX API passphrase (optional for public data).
    """

    def __init__(
        self,
        config: ExchangeInfo,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
    ) -> None:
        super().__init__("okx", config)

        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase

        self._ws_manager: WebSocketManager | None = None
        self._rate_limiter: RateLimiter = RateLimiterFactory.create("okx")
        self._exchange: ccxt.okx | None = None

        self._orderbook_symbols: dict[str, int] = {}
        self._trade_symbols: set[str] = set()

    async def connect(self) -> None:
        self._set_state(ConnectionState.CONNECTING)

        try:
            ccxt_config: dict = {
                "enableRateLimit": False,
            }
            if self._api_key:
                ccxt_config["apiKey"] = self._api_key
            if self._api_secret:
                ccxt_config["secret"] = self._api_secret
            if self._passphrase:
                ccxt_config["password"] = self._passphrase

            self._exchange = ccxt.okx(ccxt_config)

            self._set_state(ConnectionState.CONNECTED)
            self._logger.info("okx_connected")

        except Exception as e:
            self._set_state(ConnectionState.ERROR)
            self._logger.error("okx_connect_failed", error=str(e))
            raise ConnectionError(f"Failed to connect to OKX: {e}") from e

    async def disconnect(self) -> None:
        if self._ws_manager is not None:
            await self._ws_manager.disconnect()
            self._ws_manager = None

        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

        self._set_state(ConnectionState.DISCONNECTED)
        self._logger.info("okx_disconnected")

    async def subscribe_orderbook(self, symbols: list[str], depth: int = 10) -> None:
        for symbol in symbols:
            self._orderbook_symbols[symbol] = depth

        await self._ensure_ws_connected()

        # Always use books5 (5-level full snapshot per push).
        # The "books" channel sends delta updates that require local state
        # management; books5 is simpler and sufficient for arbitrage detection.
        channel = "books5"
        args = [
            {"channel": channel, "instId": _to_okx_inst_id(s)}
            for s in symbols
        ]
        subscribe_msg = {"op": "subscribe", "args": args}

        if self._ws_manager is not None:
            await self._ws_manager.send(subscribe_msg)
            self._logger.info(
                "okx_orderbook_subscribed",
                symbols=symbols,
                channel=channel,
            )

    async def subscribe_trades(self, symbols: list[str]) -> None:
        self._trade_symbols.update(symbols)

        await self._ensure_ws_connected()

        args = [
            {"channel": "trades", "instId": _to_okx_inst_id(s)}
            for s in symbols
        ]
        subscribe_msg = {"op": "subscribe", "args": args}

        if self._ws_manager is not None:
            await self._ws_manager.send(subscribe_msg)
            self._logger.info("okx_trades_subscribed", symbols=symbols)

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        if self._exchange is None:
            raise ConnectionError("Not connected to OKX")

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
                exchange="okx",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=status,
            )

            self._logger.info(
                "okx_order_placed",
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
                "okx_order_failed",
                symbol=symbol,
                side=side.value,
                error=str(e),
            )
            return Order(
                exchange="okx",
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.FAILED,
            )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        if self._exchange is None:
            raise ConnectionError("Not connected to OKX")

        await self._rate_limiter.acquire(weight=1)

        try:
            await self._exchange.cancel_order(order_id, symbol)
            self._logger.info("okx_order_cancelled", order_id=order_id, symbol=symbol)
            return True
        except ccxt.BaseError as e:
            self._logger.error(
                "okx_cancel_failed",
                order_id=order_id,
                symbol=symbol,
                error=str(e),
            )
            return False

    async def get_order_status(self, order_id: str, symbol: str) -> Order:
        if self._exchange is None:
            raise ConnectionError("Not connected to OKX")

        await self._rate_limiter.acquire(weight=1)

        result = await self._exchange.fetch_order(order_id, symbol)

        status = _CCXT_STATUS_MAP.get(result.get("status", ""), OrderStatus.SUBMITTED)
        side_str = result.get("side", "buy").upper()
        side = OrderSide.BUY if side_str == "BUY" else OrderSide.SELL
        type_str = result.get("type", "limit").upper()
        order_type = OrderType.MARKET if type_str == "MARKET" else OrderType.LIMIT

        return Order(
            id=str(result.get("id", order_id)),
            exchange="okx",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=result.get("amount", 0.0),
            price=result.get("price"),
            status=status,
        )

    async def get_balances(self) -> dict[str, AssetBalance]:
        if self._exchange is None:
            raise ConnectionError("Not connected to OKX")

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

        self._logger.info("okx_balances_fetched", asset_count=len(result))
        return result

    async def get_trading_fee(self, symbol: str) -> TradingFee:
        if self._exchange is None:
            raise ConnectionError("Not connected to OKX")

        await self._rate_limiter.acquire(weight=1)

        try:
            fees = await self._exchange.fetch_trading_fee(symbol)
            return TradingFee(
                maker_pct=float(fees.get("maker", 0.0008)) * 100,
                taker_pct=float(fees.get("taker", 0.001)) * 100,
            )
        except ccxt.BaseError:
            self._logger.warning("okx_fee_fetch_failed_using_config", symbol=symbol)
            return self.config.fees

    async def get_withdrawal_fee(self, asset: str, network: str) -> float:
        if self._exchange is None:
            raise ConnectionError("Not connected to OKX")

        await self._rate_limiter.acquire(weight=1)

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
                "okx_withdrawal_fee_not_found", asset=asset, network=network
            )
            return 0.0

        except ccxt.BaseError as e:
            self._logger.error(
                "okx_withdrawal_fee_failed",
                asset=asset,
                network=network,
                error=str(e),
            )
            return 0.0

    # --- Internal WebSocket ---

    async def _ensure_ws_connected(self) -> None:
        if self._ws_manager is not None and self._ws_manager.is_connected:
            return

        self._ws_manager = WebSocketManager(
            url=_WS_PUBLIC_URL,
            on_message=self._handle_ws_message,
            reconnect_delay=1.0,
            max_reconnect_delay=60.0,
            heartbeat_interval=25.0,
        )
        await self._ws_manager.connect()
        self._logger.info("okx_ws_connected")

    async def _handle_ws_message(self, data: dict | str) -> None:
        if not isinstance(data, dict):
            return

        # OKX subscription confirmation
        event = data.get("event")
        if event in ("subscribe", "unsubscribe", "error"):
            if event == "error":
                self._logger.warning("okx_ws_error", data=data)
            return

        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        action = data.get("action", "")
        items = data.get("data", [])

        if not items:
            return

        if channel in ("books5", "books"):
            await self._handle_orderbook(arg, items, action)
        elif channel == "trades":
            await self._handle_trades(arg, items)

    async def _handle_orderbook(
        self, arg: dict, items: list[dict], action: str
    ) -> None:
        inst_id = arg.get("instId", "")
        symbol = _to_unified_symbol(inst_id)

        for item in items:
            ts_str = item.get("ts", "")
            timestamp = float(ts_str) / 1000.0 if ts_str else time.time()

            bids = [
                OrderBookEntry(price=float(b[0]), quantity=float(b[1]))
                for b in item.get("bids", [])
                if float(b[1]) > 0
            ]
            asks = [
                OrderBookEntry(price=float(a[0]), quantity=float(a[1]))
                for a in item.get("asks", [])
                if float(a[1]) > 0
            ]

            bids.sort(key=lambda e: e.price, reverse=True)
            asks.sort(key=lambda e: e.price)

            orderbook = OrderBook(
                exchange="okx",
                symbol=symbol,
                timestamp=timestamp,
                bids=bids,
                asks=asks,
            )

            await self._notify_orderbook(orderbook)

    async def _handle_trades(self, arg: dict, items: list[dict]) -> None:
        inst_id = arg.get("instId", "")
        symbol = _to_unified_symbol(inst_id)

        for item in items:
            ts_str = item.get("ts", "")
            trade_time = float(ts_str) / 1000.0 if ts_str else time.time()
            price = float(item.get("px", 0))
            quantity = float(item.get("sz", 0))
            side_str = item.get("side", "buy")
            side = OrderSide.BUY if side_str == "buy" else OrderSide.SELL

            order = Order(
                id=str(item.get("tradeId", "")),
                exchange="okx",
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
