"""Data normalization layer for cross-exchange data integration.

Converts exchange-specific raw data formats into unified internal models
(OrderBook, TradeResult) with consistent symbol formatting and UTC timestamps.
"""

import time
from datetime import datetime, timezone

from arbot.logging import get_logger
from arbot.models import (
    Order,
    OrderBook,
    OrderBookEntry,
    OrderSide,
    OrderStatus,
    OrderType,
    TradeResult,
)

logger = get_logger("normalizer")

# Exchange-specific quote assets used for symbol parsing
_QUOTE_ASSETS = ("USDT", "BUSD", "USDC", "KRW", "BTC", "ETH", "BNB")


def normalize_symbol(exchange: str, raw_symbol: str) -> str:
    """Convert an exchange-specific symbol to unified format.

    Args:
        exchange: Exchange identifier (e.g. "binance", "upbit").
        raw_symbol: Raw symbol from the exchange.

    Returns:
        Unified symbol (e.g. "BTC/USDT").

    Examples:
        >>> normalize_symbol("binance", "BTCUSDT")
        'BTC/USDT'
        >>> normalize_symbol("binance", "btcusdt")
        'BTC/USDT'
        >>> normalize_symbol("upbit", "KRW-BTC")
        'BTC/KRW'
        >>> normalize_symbol("okx", "BTC-USDT")
        'BTC/USDT'
    """
    exchange_lower = exchange.lower()

    if exchange_lower == "upbit":
        # Upbit format: "KRW-BTC" -> "BTC/KRW"
        parts = raw_symbol.split("-")
        if len(parts) == 2:
            return f"{parts[1].upper()}/{parts[0].upper()}"
        return raw_symbol.upper()

    if exchange_lower in ("okx", "bybit", "kucoin", "gate", "bitget"):
        # Dash-separated: "BTC-USDT" -> "BTC/USDT"
        parts = raw_symbol.split("-")
        if len(parts) == 2:
            return f"{parts[0].upper()}/{parts[1].upper()}"
        # Fall through to concatenated parsing

    if "/" in raw_symbol:
        # Already in unified format
        parts = raw_symbol.split("/")
        return f"{parts[0].upper()}/{parts[1].upper()}"

    # Concatenated format (e.g. "BTCUSDT") - try to split by known quotes
    s = raw_symbol.upper()
    for quote in _QUOTE_ASSETS:
        if s.endswith(quote):
            base = s[: -len(quote)]
            if base:
                return f"{base}/{quote}"

    return s


def normalize_orderbook(exchange: str, raw_data: dict) -> OrderBook:
    """Convert exchange-specific raw orderbook data to unified OrderBook.

    Supports the following exchange formats:

    Binance depthUpdate:
        {"e":"depthUpdate", "s":"BTCUSDT", "E":ts, "b":[[price,qty]], "a":[[price,qty]]}

    Binance partial depth:
        {"bids":[[price,qty]], "asks":[[price,qty]], "lastUpdateId":123}

    Upbit orderbook:
        {"type":"orderbook", "code":"KRW-BTC", "timestamp":ts,
         "orderbook_units":[{"ask_price":..., "bid_price":..., "ask_size":..., "bid_size":...}]}

    Generic (OKX, Bybit, etc.):
        {"bids":[[price,qty]], "asks":[[price,qty]]}

    Args:
        exchange: Exchange identifier.
        raw_data: Raw orderbook data dict from the exchange.

    Returns:
        Normalized OrderBook model.
    """
    exchange_lower = exchange.lower()

    if exchange_lower == "upbit" and "orderbook_units" in raw_data:
        return _normalize_upbit_orderbook(raw_data)

    if exchange_lower == "binance" and raw_data.get("e") == "depthUpdate":
        return _normalize_binance_depth(raw_data)

    # Generic format with "bids" and "asks" arrays
    return _normalize_generic_orderbook(exchange, raw_data)


def normalize_trade(exchange: str, raw_data: dict) -> TradeResult:
    """Convert exchange-specific raw trade data to unified TradeResult.

    Supports:

    Binance trade:
        {"e":"trade", "s":"BTCUSDT", "t":trade_id, "p":"50000", "q":"0.5",
         "T":timestamp_ms, "m":true/false}

    Upbit trade:
        {"type":"trade", "code":"KRW-BTC", "trade_price":50000,
         "trade_volume":0.5, "ask_bid":"ASK"/"BID", "trade_timestamp":ts_ms,
         "sequential_id":123}

    Generic:
        {"symbol":"BTC/USDT", "price":50000, "amount":0.5, "side":"buy"/"sell",
         "timestamp":ts_ms, "id":"123"}

    Args:
        exchange: Exchange identifier.
        raw_data: Raw trade data dict from the exchange.

    Returns:
        Normalized TradeResult model.
    """
    exchange_lower = exchange.lower()

    if exchange_lower == "upbit" and "trade_price" in raw_data:
        return _normalize_upbit_trade(raw_data)

    if exchange_lower == "binance" and raw_data.get("e") == "trade":
        return _normalize_binance_trade(raw_data)

    return _normalize_generic_trade(exchange, raw_data)


# --- Binance Normalizers ---


def _normalize_binance_depth(data: dict) -> OrderBook:
    """Normalize a Binance depthUpdate message."""
    raw_symbol = data.get("s", "")
    symbol = normalize_symbol("binance", raw_symbol)
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

    bids.sort(key=lambda e: e.price, reverse=True)
    asks.sort(key=lambda e: e.price)

    return OrderBook(
        exchange="binance",
        symbol=symbol,
        timestamp=timestamp,
        bids=bids,
        asks=asks,
    )


def _normalize_binance_trade(data: dict) -> TradeResult:
    """Normalize a Binance trade event message."""
    raw_symbol = data.get("s", "")
    symbol = normalize_symbol("binance", raw_symbol)
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

    return TradeResult(
        order=order,
        filled_quantity=quantity,
        filled_price=price,
        fee=0.0,
        fee_asset="",
        latency_ms=0.0,
        filled_at=datetime.fromtimestamp(trade_time, tz=timezone.utc),
    )


# --- Upbit Normalizers ---


def _normalize_upbit_orderbook(data: dict) -> OrderBook:
    """Normalize an Upbit orderbook message."""
    market_code = data.get("code", "")
    symbol = normalize_symbol("upbit", market_code)
    timestamp = float(data.get("timestamp", time.time() * 1000)) / 1000.0

    bids: list[OrderBookEntry] = []
    asks: list[OrderBookEntry] = []

    for unit in data.get("orderbook_units", []):
        bid_price = float(unit.get("bid_price", 0))
        bid_size = float(unit.get("bid_size", 0))
        ask_price = float(unit.get("ask_price", 0))
        ask_size = float(unit.get("ask_size", 0))

        if bid_size > 0:
            bids.append(OrderBookEntry(price=bid_price, quantity=bid_size))
        if ask_size > 0:
            asks.append(OrderBookEntry(price=ask_price, quantity=ask_size))

    bids.sort(key=lambda e: e.price, reverse=True)
    asks.sort(key=lambda e: e.price)

    return OrderBook(
        exchange="upbit",
        symbol=symbol,
        timestamp=timestamp,
        bids=bids,
        asks=asks,
    )


def _normalize_upbit_trade(data: dict) -> TradeResult:
    """Normalize an Upbit trade message."""
    market_code = data.get("code", "")
    symbol = normalize_symbol("upbit", market_code)
    price = float(data.get("trade_price", 0))
    quantity = float(data.get("trade_volume", 0))
    ask_bid = data.get("ask_bid", "").upper()
    trade_time = float(data.get("trade_timestamp", time.time() * 1000)) / 1000.0

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

    return TradeResult(
        order=order,
        filled_quantity=quantity,
        filled_price=price,
        fee=0.0,
        fee_asset="",
        latency_ms=0.0,
        filled_at=datetime.fromtimestamp(trade_time, tz=timezone.utc),
    )


# --- Generic Normalizers ---


def _normalize_generic_orderbook(exchange: str, data: dict) -> OrderBook:
    """Normalize a generic orderbook with bids/asks arrays."""
    # Try to extract symbol
    raw_symbol = data.get("symbol", data.get("s", ""))
    symbol = normalize_symbol(exchange, raw_symbol) if raw_symbol else ""

    # Timestamp: try multiple common field names
    ts_raw = data.get("timestamp", data.get("ts", data.get("E", data.get("lastUpdateId", 0))))
    timestamp = float(ts_raw)
    # If timestamp looks like milliseconds, convert to seconds
    if timestamp > 1e12:
        timestamp = timestamp / 1000.0

    bids = [
        OrderBookEntry(price=float(b[0]), quantity=float(b[1]))
        for b in data.get("bids", data.get("b", []))
        if float(b[1]) > 0
    ]
    asks = [
        OrderBookEntry(price=float(a[0]), quantity=float(a[1]))
        for a in data.get("asks", data.get("a", []))
        if float(a[1]) > 0
    ]

    bids.sort(key=lambda e: e.price, reverse=True)
    asks.sort(key=lambda e: e.price)

    return OrderBook(
        exchange=exchange.lower(),
        symbol=symbol,
        timestamp=timestamp,
        bids=bids,
        asks=asks,
    )


def _normalize_generic_trade(exchange: str, data: dict) -> TradeResult:
    """Normalize a generic trade event."""
    raw_symbol = data.get("symbol", data.get("s", ""))
    symbol = normalize_symbol(exchange, raw_symbol) if raw_symbol else ""

    price = float(data.get("price", data.get("p", 0)))
    quantity = float(data.get("amount", data.get("q", data.get("quantity", 0))))

    side_str = str(data.get("side", "buy")).upper()
    side = OrderSide.SELL if side_str == "SELL" else OrderSide.BUY

    ts_raw = data.get("timestamp", data.get("ts", data.get("T", time.time() * 1000)))
    ts = float(ts_raw)
    if ts > 1e12:
        ts = ts / 1000.0

    trade_id = str(data.get("id", data.get("t", "")))

    order = Order(
        id=trade_id,
        exchange=exchange.lower(),
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=price,
        status=OrderStatus.FILLED,
    )

    return TradeResult(
        order=order,
        filled_quantity=quantity,
        filled_price=price,
        fee=0.0,
        fee_asset="",
        latency_ms=0.0,
        filled_at=datetime.fromtimestamp(ts, tz=timezone.utc),
    )
