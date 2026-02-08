"""Unit tests for the triangular arbitrage detector."""

import pytest

from arbot.detector.triangular import TriangularDetector
from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.models.signal import ArbitrageStrategy, SignalStatus


def _make_ob(
    exchange: str,
    symbol: str,
    best_bid: float,
    best_ask: float,
    depth_qty: float = 10.0,
) -> OrderBook:
    """Helper to create a simple OrderBook."""
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        timestamp=1700000000.0,
        bids=[
            OrderBookEntry(price=best_bid, quantity=depth_qty),
            OrderBookEntry(price=best_bid * 0.999, quantity=depth_qty),
        ],
        asks=[
            OrderBookEntry(price=best_ask, quantity=depth_qty),
            OrderBookEntry(price=best_ask * 1.001, quantity=depth_qty),
        ],
    )


@pytest.fixture
def low_fee() -> TradingFee:
    return TradingFee(maker_pct=0.02, taker_pct=0.04)


# ---------------------------------------------------------------------------
# Path finding
# ---------------------------------------------------------------------------


class TestFindPaths:
    """Tests for _find_triangular_paths."""

    def test_valid_triangle(self) -> None:
        detector = TriangularDetector()
        symbols = ["BTC/USDT", "ETH/BTC", "ETH/USDT"]
        paths = detector._find_triangular_paths(symbols)
        assert len(paths) == 1
        assert set(paths[0]) == {"BTC/USDT", "ETH/BTC", "ETH/USDT"}

    def test_no_valid_triangle(self) -> None:
        detector = TriangularDetector()
        # These 3 symbols involve 4 distinct assets, not a valid triangle
        symbols = ["BTC/USDT", "ETH/EUR", "SOL/JPY"]
        paths = detector._find_triangular_paths(symbols)
        assert len(paths) == 0

    def test_multiple_triangles(self) -> None:
        detector = TriangularDetector()
        symbols = ["BTC/USDT", "ETH/BTC", "ETH/USDT", "SOL/USDT", "SOL/BTC"]
        paths = detector._find_triangular_paths(symbols)
        # BTC/USDT + ETH/BTC + ETH/USDT = 1 triangle
        # BTC/USDT + SOL/BTC + SOL/USDT = 1 triangle
        assert len(paths) == 2

    def test_two_symbols_no_triangle(self) -> None:
        detector = TriangularDetector()
        paths = detector._find_triangular_paths(["BTC/USDT", "ETH/USDT"])
        assert len(paths) == 0

    def test_single_symbol_no_triangle(self) -> None:
        detector = TriangularDetector()
        paths = detector._find_triangular_paths(["BTC/USDT"])
        assert len(paths) == 0


# ---------------------------------------------------------------------------
# Profitable detection
# ---------------------------------------------------------------------------


class TestDetectProfitable:
    """Tests for detecting profitable triangular arbitrage."""

    def test_profitable_triangle(self, low_fee: TradingFee) -> None:
        """Set up prices that create a profitable triangle.

        Path: USDT -> BTC -> ETH -> USDT
        Leg 1: Buy BTC/USDT at ask 50000 => 1000/50000 = 0.02 BTC (- fee)
        Leg 2: Buy ETH/BTC at ask 0.05 => 0.02/0.05 = 0.4 ETH (- fee)
        Leg 3: Sell ETH/USDT at bid 2600 => 0.4 * 2600 = 1040 (- fee)
        With very low fee (0.04%), should be profitable if final > 1000.
        """
        orderbooks = {
            "BTC/USDT": _make_ob("binance", "BTC/USDT", best_bid=50000, best_ask=50000),
            "ETH/BTC": _make_ob("binance", "ETH/BTC", best_bid=0.05, best_ask=0.05),
            "ETH/USDT": _make_ob("binance", "ETH/USDT", best_bid=2600, best_ask=2550),
        }
        # ETH: buy via BTC at 0.05 BTC = 2500 USDT equivalent
        # ETH: sell at 2600 USDT => ~4% gross profit before fees
        detector = TriangularDetector(min_profit_pct=0.1, default_fee=low_fee)
        signals = detector.detect(orderbooks, exchange="binance", quantity_usd=1000.0)

        assert len(signals) >= 1
        signal = signals[0]
        assert signal.strategy == ArbitrageStrategy.TRIANGULAR
        assert signal.buy_exchange == "binance"
        assert signal.sell_exchange == "binance"
        assert signal.status == SignalStatus.DETECTED
        assert signal.net_spread_pct >= 0.1
        assert signal.estimated_profit_usd > 0
        assert signal.metadata is not None
        assert "path" in signal.metadata

    def test_no_profitable_triangle(self, low_fee: TradingFee) -> None:
        """Prices that do NOT create a profitable triangle."""
        orderbooks = {
            "BTC/USDT": _make_ob("binance", "BTC/USDT", best_bid=50000, best_ask=50000),
            "ETH/BTC": _make_ob("binance", "ETH/BTC", best_bid=0.05, best_ask=0.05),
            "ETH/USDT": _make_ob("binance", "ETH/USDT", best_bid=2500, best_ask=2500),
        }
        # Fair pricing: no arbitrage opportunity
        detector = TriangularDetector(min_profit_pct=0.15, default_fee=low_fee)
        signals = detector.detect(orderbooks, exchange="binance", quantity_usd=1000.0)
        assert len(signals) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_orderbook_skipped(self, low_fee: TradingFee) -> None:
        orderbooks = {
            "BTC/USDT": _make_ob("binance", "BTC/USDT", best_bid=50000, best_ask=50000),
            "ETH/BTC": OrderBook(
                exchange="binance", symbol="ETH/BTC", timestamp=1700000000.0
            ),
            "ETH/USDT": _make_ob("binance", "ETH/USDT", best_bid=2600, best_ask=2550),
        }
        detector = TriangularDetector(min_profit_pct=0.1, default_fee=low_fee)
        signals = detector.detect(orderbooks, exchange="binance", quantity_usd=1000.0)
        assert len(signals) == 0

    def test_no_symbols_returns_empty(self, low_fee: TradingFee) -> None:
        detector = TriangularDetector(min_profit_pct=0.1, default_fee=low_fee)
        signals = detector.detect({}, exchange="binance", quantity_usd=1000.0)
        assert len(signals) == 0

    def test_default_fee_used(self) -> None:
        """Without explicit fee, default 0.1% taker is used."""
        detector = TriangularDetector(min_profit_pct=0.1)
        assert detector.default_fee.taker_pct == 0.1


# ---------------------------------------------------------------------------
# Signal fields
# ---------------------------------------------------------------------------


class TestSignalFields:
    """Tests for correct signal population."""

    def test_signal_metadata_has_path(self, low_fee: TradingFee) -> None:
        orderbooks = {
            "BTC/USDT": _make_ob("binance", "BTC/USDT", best_bid=50000, best_ask=50000),
            "ETH/BTC": _make_ob("binance", "ETH/BTC", best_bid=0.05, best_ask=0.05),
            "ETH/USDT": _make_ob("binance", "ETH/USDT", best_bid=2600, best_ask=2550),
        }
        detector = TriangularDetector(min_profit_pct=0.1, default_fee=low_fee)
        signals = detector.detect(orderbooks, exchange="binance", quantity_usd=1000.0)

        assert len(signals) >= 1
        signal = signals[0]
        assert signal.metadata is not None
        assert "path" in signal.metadata
        assert "directions" in signal.metadata
        assert len(signal.metadata["path"]) == 3
        assert len(signal.metadata["directions"]) == 3
        assert all(d in ("buy", "sell") for d in signal.metadata["directions"])

    def test_signal_confidence_bounded(self, low_fee: TradingFee) -> None:
        orderbooks = {
            "BTC/USDT": _make_ob("binance", "BTC/USDT", best_bid=50000, best_ask=50000),
            "ETH/BTC": _make_ob("binance", "ETH/BTC", best_bid=0.05, best_ask=0.05),
            "ETH/USDT": _make_ob("binance", "ETH/USDT", best_bid=2600, best_ask=2550),
        }
        detector = TriangularDetector(min_profit_pct=0.1, default_fee=low_fee)
        signals = detector.detect(orderbooks, exchange="binance", quantity_usd=1000.0)

        for signal in signals:
            assert 0.0 < signal.confidence <= 1.0

    def test_signals_sorted_by_net_spread(self, low_fee: TradingFee) -> None:
        orderbooks = {
            "BTC/USDT": _make_ob("binance", "BTC/USDT", best_bid=50000, best_ask=50000),
            "ETH/BTC": _make_ob("binance", "ETH/BTC", best_bid=0.05, best_ask=0.05),
            "ETH/USDT": _make_ob("binance", "ETH/USDT", best_bid=2600, best_ask=2550),
            "SOL/USDT": _make_ob("binance", "SOL/USDT", best_bid=130, best_ask=125),
            "SOL/BTC": _make_ob("binance", "SOL/BTC", best_bid=0.0025, best_ask=0.0025),
        }
        detector = TriangularDetector(min_profit_pct=0.05, default_fee=low_fee)
        signals = detector.detect(orderbooks, exchange="binance", quantity_usd=1000.0)

        for i in range(len(signals) - 1):
            assert signals[i].net_spread_pct >= signals[i + 1].net_spread_pct
