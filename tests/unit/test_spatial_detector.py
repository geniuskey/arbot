"""Unit tests for the spatial arbitrage detector."""

import pytest

from arbot.detector.spatial import SpatialDetector
from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.models.signal import ArbitrageStrategy, SignalStatus


def _make_orderbook(
    exchange: str,
    best_bid: float,
    best_ask: float,
    depth_qty: float = 2.0,
) -> OrderBook:
    """Helper to create an OrderBook with 3 levels on each side."""
    return OrderBook(
        exchange=exchange,
        symbol="BTC/USDT",
        timestamp=1700000000.0,
        bids=[
            OrderBookEntry(price=best_bid, quantity=depth_qty),
            OrderBookEntry(price=best_bid - 10, quantity=depth_qty),
            OrderBookEntry(price=best_bid - 20, quantity=depth_qty),
        ],
        asks=[
            OrderBookEntry(price=best_ask, quantity=depth_qty),
            OrderBookEntry(price=best_ask + 10, quantity=depth_qty),
            OrderBookEntry(price=best_ask + 20, quantity=depth_qty),
        ],
    )


@pytest.fixture
def fees() -> dict[str, TradingFee]:
    return {
        "binance": TradingFee(maker_pct=0.02, taker_pct=0.04),
        "upbit": TradingFee(maker_pct=0.05, taker_pct=0.05),
        "okx": TradingFee(maker_pct=0.02, taker_pct=0.03),
    }


# ---------------------------------------------------------------------------
# Opportunity detection
# ---------------------------------------------------------------------------


class TestDetectOpportunity:
    """Tests for detecting profitable spatial arbitrage."""

    def test_detects_opportunity_with_sufficient_spread(
        self, fees: dict[str, TradingFee]
    ) -> None:
        # binance ask=50000, upbit bid=50300 => gross ~0.6%, net ~0.51%
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=49900, best_ask=50000),
            "upbit": _make_orderbook("upbit", best_bid=50300, best_ask=50400),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.25, default_quantity_usd=10000.0
        )
        signals = detector.detect(orderbooks)

        assert len(signals) >= 1
        # The best signal should be buy binance, sell upbit
        best = signals[0]
        assert best.buy_exchange == "binance"
        assert best.sell_exchange == "upbit"
        assert best.strategy == ArbitrageStrategy.SPATIAL
        assert best.status == SignalStatus.DETECTED
        assert best.net_spread_pct >= 0.25
        assert best.estimated_profit_usd > 0
        assert best.symbol == "BTC/USDT"

    def test_no_opportunity_when_spread_insufficient(
        self, fees: dict[str, TradingFee]
    ) -> None:
        # Nearly identical prices => no opportunity
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=50000, best_ask=50010),
            "upbit": _make_orderbook("upbit", best_bid=50005, best_ask=50015),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.25, default_quantity_usd=10000.0
        )
        signals = detector.detect(orderbooks)

        assert len(signals) == 0


# ---------------------------------------------------------------------------
# Multi-exchange combinations
# ---------------------------------------------------------------------------


class TestMultiExchange:
    """Tests for multiple exchange pair scanning."""

    def test_three_exchanges_finds_best_pair(
        self, fees: dict[str, TradingFee]
    ) -> None:
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=49900, best_ask=50000),
            "upbit": _make_orderbook("upbit", best_bid=50300, best_ask=50400),
            "okx": _make_orderbook("okx", best_bid=50150, best_ask=50200),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.25, default_quantity_usd=10000.0
        )
        signals = detector.detect(orderbooks)

        assert len(signals) >= 1
        # Sorted by net_spread descending, best should be binance->upbit
        best = signals[0]
        assert best.buy_exchange == "binance"
        assert best.sell_exchange == "upbit"

    def test_signals_sorted_by_net_spread_desc(
        self, fees: dict[str, TradingFee]
    ) -> None:
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=49900, best_ask=50000),
            "upbit": _make_orderbook("upbit", best_bid=50300, best_ask=50400),
            "okx": _make_orderbook("okx", best_bid=50200, best_ask=50250),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.1, default_quantity_usd=10000.0
        )
        signals = detector.detect(orderbooks)

        for i in range(len(signals) - 1):
            assert signals[i].net_spread_pct >= signals[i + 1].net_spread_pct

    def test_single_exchange_no_signals(
        self, fees: dict[str, TradingFee]
    ) -> None:
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=50000, best_ask=50010),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.25, default_quantity_usd=10000.0
        )
        signals = detector.detect(orderbooks)
        assert len(signals) == 0


# ---------------------------------------------------------------------------
# Depth filtering
# ---------------------------------------------------------------------------


class TestDepthFiltering:
    """Tests for filtering by minimum order book depth."""

    def test_insufficient_depth_filtered_out(
        self, fees: dict[str, TradingFee]
    ) -> None:
        # Very small depth (0.001 qty at ~50000 => ~50 USD per level)
        orderbooks = {
            "binance": _make_orderbook(
                "binance", best_bid=49900, best_ask=50000, depth_qty=0.001
            ),
            "upbit": _make_orderbook(
                "upbit", best_bid=50300, best_ask=50400, depth_qty=0.001
            ),
        }
        # min_depth_usd=1000 but available depth is ~150 USD total
        detector = SpatialDetector(
            exchange_fees=fees,
            min_spread_pct=0.25,
            min_depth_usd=1000.0,
            default_quantity_usd=100.0,
        )
        signals = detector.detect(orderbooks)
        assert len(signals) == 0

    def test_sufficient_depth_passes(
        self, fees: dict[str, TradingFee]
    ) -> None:
        # Large depth (10 BTC per level at ~50000 => ~500000 USD per level)
        orderbooks = {
            "binance": _make_orderbook(
                "binance", best_bid=49900, best_ask=50000, depth_qty=10.0
            ),
            "upbit": _make_orderbook(
                "upbit", best_bid=50300, best_ask=50400, depth_qty=10.0
            ),
        }
        detector = SpatialDetector(
            exchange_fees=fees,
            min_spread_pct=0.25,
            min_depth_usd=1000.0,
            default_quantity_usd=10000.0,
        )
        signals = detector.detect(orderbooks)
        assert len(signals) >= 1
        assert all(s.orderbook_depth_usd >= 1000.0 for s in signals)


# ---------------------------------------------------------------------------
# Signal fields
# ---------------------------------------------------------------------------


class TestSignalFields:
    """Tests for correct ArbitrageSignal field population."""

    def test_signal_has_correct_fields(
        self, fees: dict[str, TradingFee]
    ) -> None:
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=49900, best_ask=50000),
            "upbit": _make_orderbook("upbit", best_bid=50300, best_ask=50400),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.25, default_quantity_usd=10000.0
        )
        signals = detector.detect(orderbooks)

        assert len(signals) >= 1
        signal = signals[0]
        assert signal.id is not None
        assert signal.detected_at is not None
        assert signal.executed_at is None
        assert signal.confidence > 0.0
        assert signal.confidence <= 1.0
        assert signal.quantity > 0.0
        assert signal.gross_spread_pct > signal.net_spread_pct  # fees reduce spread

    def test_empty_orderbook_ignored(
        self, fees: dict[str, TradingFee]
    ) -> None:
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=49900, best_ask=50000),
            "upbit": OrderBook(
                exchange="upbit", symbol="BTC/USDT", timestamp=1700000000.0
            ),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.25, default_quantity_usd=10000.0
        )
        signals = detector.detect(orderbooks)
        assert len(signals) == 0

    def test_default_fee_used_for_unknown_exchange(self) -> None:
        fees = {"binance": TradingFee(maker_pct=0.02, taker_pct=0.04)}
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=49900, best_ask=50000),
            "unknown_ex": _make_orderbook("unknown_ex", best_bid=50300, best_ask=50400),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.25, default_quantity_usd=10000.0
        )
        signals = detector.detect(orderbooks)
        # Should still work with default fee for unknown exchange
        assert len(signals) >= 1

    def test_default_quantity_usd_used(
        self, fees: dict[str, TradingFee]
    ) -> None:
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=49900, best_ask=50000),
            "upbit": _make_orderbook("upbit", best_bid=50300, best_ask=50400),
        }
        detector = SpatialDetector(
            exchange_fees=fees, min_spread_pct=0.25, default_quantity_usd=5000.0
        )
        assert detector.default_quantity_usd == 5000.0
        signals = detector.detect(orderbooks)
        assert len(signals) >= 1

    def test_no_exchange_fees_defaults(self) -> None:
        orderbooks = {
            "binance": _make_orderbook("binance", best_bid=49900, best_ask=50000),
            "upbit": _make_orderbook("upbit", best_bid=50300, best_ask=50400),
        }
        detector = SpatialDetector(min_spread_pct=0.25, default_quantity_usd=10000.0)
        signals = detector.detect(orderbooks)
        # All exchanges use default 0.1% fee
        assert len(signals) >= 1
