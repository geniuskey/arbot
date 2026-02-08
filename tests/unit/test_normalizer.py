"""Unit tests for the data normalizer module."""

import pytest

from arbot.connectors.normalizer import (
    normalize_orderbook,
    normalize_symbol,
    normalize_trade,
)
from arbot.models import OrderSide


# ---------------------------------------------------------------------------
# Symbol normalization tests
# ---------------------------------------------------------------------------


class TestNormalizeSymbol:
    """Tests for cross-exchange symbol normalization."""

    # Binance: concatenated lowercase/uppercase -> "BASE/QUOTE"
    def test_binance_btcusdt(self) -> None:
        assert normalize_symbol("binance", "BTCUSDT") == "BTC/USDT"

    def test_binance_btcusdt_lower(self) -> None:
        assert normalize_symbol("binance", "btcusdt") == "BTC/USDT"

    def test_binance_ethbtc(self) -> None:
        assert normalize_symbol("binance", "ETHBTC") == "ETH/BTC"

    def test_binance_bnbusdt(self) -> None:
        assert normalize_symbol("binance", "BNBUSDT") == "BNB/USDT"

    # Upbit: "KRW-BTC" -> "BTC/KRW"
    def test_upbit_krw_btc(self) -> None:
        assert normalize_symbol("upbit", "KRW-BTC") == "BTC/KRW"

    def test_upbit_krw_eth(self) -> None:
        assert normalize_symbol("upbit", "KRW-ETH") == "ETH/KRW"

    def test_upbit_usdt_btc(self) -> None:
        assert normalize_symbol("upbit", "USDT-BTC") == "BTC/USDT"

    # OKX: "BTC-USDT" -> "BTC/USDT"
    def test_okx_btc_usdt(self) -> None:
        assert normalize_symbol("okx", "BTC-USDT") == "BTC/USDT"

    def test_okx_eth_usdt(self) -> None:
        assert normalize_symbol("okx", "ETH-USDT") == "ETH/USDT"

    # Bybit: "BTC-USDT" -> "BTC/USDT"
    def test_bybit_btc_usdt(self) -> None:
        assert normalize_symbol("bybit", "BTC-USDT") == "BTC/USDT"

    # Already unified format
    def test_already_unified(self) -> None:
        assert normalize_symbol("binance", "BTC/USDT") == "BTC/USDT"

    def test_already_unified_lowercase(self) -> None:
        assert normalize_symbol("binance", "btc/usdt") == "BTC/USDT"

    # KuCoin
    def test_kucoin_dash_separated(self) -> None:
        assert normalize_symbol("kucoin", "BTC-USDT") == "BTC/USDT"

    # Gate.io
    def test_gate_dash_separated(self) -> None:
        assert normalize_symbol("gate", "BTC-USDT") == "BTC/USDT"

    # Unknown format fallback
    def test_unknown_format(self) -> None:
        result = normalize_symbol("binance", "XYZABC")
        assert result == "XYZABC"

    def test_ethusdc(self) -> None:
        assert normalize_symbol("binance", "ETHUSDC") == "ETH/USDC"

    def test_solkrw_upbit(self) -> None:
        assert normalize_symbol("upbit", "KRW-SOL") == "SOL/KRW"


# ---------------------------------------------------------------------------
# OrderBook normalization tests
# ---------------------------------------------------------------------------


class TestNormalizeOrderbook:
    """Tests for cross-exchange orderbook normalization."""

    def test_binance_depth_update(self) -> None:
        raw = {
            "e": "depthUpdate",
            "s": "BTCUSDT",
            "E": 1700000000000,
            "b": [["50000.00", "1.5"], ["49999.00", "2.0"]],
            "a": [["50001.00", "1.0"], ["50002.00", "3.0"]],
        }

        ob = normalize_orderbook("binance", raw)
        assert ob.exchange == "binance"
        assert ob.symbol == "BTC/USDT"
        assert ob.timestamp == 1700000000.0
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        assert ob.bids[0].price == 50000.0
        assert ob.asks[0].price == 50001.0

    def test_binance_depth_filters_zero(self) -> None:
        raw = {
            "e": "depthUpdate",
            "s": "ETHUSDT",
            "E": 1700000000000,
            "b": [["3000.00", "0"], ["2999.00", "5.0"]],
            "a": [["3001.00", "2.0"]],
        }

        ob = normalize_orderbook("binance", raw)
        assert len(ob.bids) == 1
        assert ob.bids[0].price == 2999.0

    def test_upbit_orderbook(self) -> None:
        raw = {
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

        ob = normalize_orderbook("upbit", raw)
        assert ob.exchange == "upbit"
        assert ob.symbol == "BTC/KRW"
        assert ob.timestamp == 1700000000.0
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        assert ob.bids[0].price == 50000000.0
        assert ob.asks[0].price == 50001000.0

    def test_upbit_orderbook_filters_zero(self) -> None:
        raw = {
            "type": "orderbook",
            "code": "KRW-ETH",
            "timestamp": 1700000000000,
            "orderbook_units": [
                {"ask_price": 3000000.0, "bid_price": 2999000.0,
                 "ask_size": 0, "bid_size": 5.0},
            ],
        }

        ob = normalize_orderbook("upbit", raw)
        assert len(ob.bids) == 1
        assert len(ob.asks) == 0

    def test_generic_orderbook(self) -> None:
        raw = {
            "symbol": "BTC-USDT",
            "timestamp": 1700000000000,
            "bids": [["50000", "1.0"]],
            "asks": [["50001", "1.0"]],
        }

        ob = normalize_orderbook("okx", raw)
        assert ob.exchange == "okx"
        assert ob.symbol == "BTC/USDT"
        assert ob.timestamp == 1700000000.0
        assert len(ob.bids) == 1
        assert len(ob.asks) == 1

    def test_generic_orderbook_sorts(self) -> None:
        raw = {
            "symbol": "BTC/USDT",
            "timestamp": 1700000000.0,
            "bids": [["49000", "1.0"], ["50000", "1.0"]],
            "asks": [["51000", "1.0"], ["50100", "1.0"]],
        }

        ob = normalize_orderbook("bybit", raw)
        # Bids descending
        assert ob.bids[0].price == 50000.0
        assert ob.bids[1].price == 49000.0
        # Asks ascending
        assert ob.asks[0].price == 50100.0
        assert ob.asks[1].price == 51000.0


# ---------------------------------------------------------------------------
# Trade normalization tests
# ---------------------------------------------------------------------------


class TestNormalizeTrade:
    """Tests for cross-exchange trade normalization."""

    def test_binance_trade_buy(self) -> None:
        raw = {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 12345,
            "p": "50000.00",
            "q": "0.5",
            "T": 1700000000000,
            "m": False,
        }

        tr = normalize_trade("binance", raw)
        assert tr.order.exchange == "binance"
        assert tr.order.symbol == "BTC/USDT"
        assert tr.order.side == OrderSide.BUY
        assert tr.filled_price == 50000.0
        assert tr.filled_quantity == 0.5

    def test_binance_trade_sell(self) -> None:
        raw = {
            "e": "trade",
            "s": "ETHUSDT",
            "t": 67890,
            "p": "3000.00",
            "q": "10.0",
            "T": 1700000000000,
            "m": True,
        }

        tr = normalize_trade("binance", raw)
        assert tr.order.side == OrderSide.SELL
        assert tr.order.symbol == "ETH/USDT"

    def test_upbit_trade_bid(self) -> None:
        raw = {
            "type": "trade",
            "code": "KRW-BTC",
            "trade_price": 50000000.0,
            "trade_volume": 0.5,
            "ask_bid": "BID",
            "trade_timestamp": 1700000000000,
            "sequential_id": 12345,
        }

        tr = normalize_trade("upbit", raw)
        assert tr.order.exchange == "upbit"
        assert tr.order.symbol == "BTC/KRW"
        assert tr.order.side == OrderSide.BUY
        assert tr.filled_price == 50000000.0
        assert tr.filled_quantity == 0.5

    def test_upbit_trade_ask(self) -> None:
        raw = {
            "type": "trade",
            "code": "KRW-ETH",
            "trade_price": 3000000.0,
            "trade_volume": 10.0,
            "ask_bid": "ASK",
            "trade_timestamp": 1700000000000,
            "sequential_id": 67890,
        }

        tr = normalize_trade("upbit", raw)
        assert tr.order.side == OrderSide.SELL
        assert tr.order.symbol == "ETH/KRW"

    def test_generic_trade_buy(self) -> None:
        raw = {
            "symbol": "BTC-USDT",
            "price": 50000.0,
            "amount": 0.5,
            "side": "buy",
            "timestamp": 1700000000000,
            "id": "abc123",
        }

        tr = normalize_trade("okx", raw)
        assert tr.order.exchange == "okx"
        assert tr.order.symbol == "BTC/USDT"
        assert tr.order.side == OrderSide.BUY
        assert tr.filled_price == 50000.0

    def test_generic_trade_sell(self) -> None:
        raw = {
            "symbol": "ETH-USDT",
            "price": 3000.0,
            "amount": 10.0,
            "side": "sell",
            "timestamp": 1700000000000,
            "id": "def456",
        }

        tr = normalize_trade("okx", raw)
        assert tr.order.side == OrderSide.SELL

    def test_timestamp_utc(self) -> None:
        raw = {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 1,
            "p": "50000",
            "q": "1",
            "T": 1700000000000,
            "m": False,
        }

        tr = normalize_trade("binance", raw)
        assert tr.filled_at.tzinfo is not None  # Has timezone info
        assert tr.filled_at.tzname() == "UTC"
