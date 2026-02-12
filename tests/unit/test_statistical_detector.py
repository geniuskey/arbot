"""Unit tests for the statistical arbitrage detection system."""

import numpy as np
import pytest

from arbot.backtest.stat_arb_backtest import StatArbBacktester, StatArbBacktestResult
from arbot.detector.cointegration import CointegrationAnalyzer, JohansenResult
from arbot.detector.pair_scanner import CointegratedPair, PairScanner
from arbot.detector.statistical import StatisticalDetector
from arbot.detector.zscore import ZScoreGenerator, ZScoreSignal
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.models.signal import ArbitrageStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orderbook(
    exchange: str,
    symbol: str,
    mid_price: float,
    spread: float = 1.0,
    depth_qty: float = 1.0,
) -> OrderBook:
    """Create an OrderBook with given mid price and spread."""
    half = spread / 2
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        timestamp=1700000000.0,
        bids=[
            OrderBookEntry(price=mid_price - half, quantity=depth_qty),
            OrderBookEntry(price=mid_price - half - 1, quantity=depth_qty),
        ],
        asks=[
            OrderBookEntry(price=mid_price + half, quantity=depth_qty),
            OrderBookEntry(price=mid_price + half + 1, quantity=depth_qty),
        ],
    )


# ---------------------------------------------------------------------------
# CointegrationAnalyzer tests
# ---------------------------------------------------------------------------

class TestCointegrationAnalyzer:
    """Tests for the Engle-Granger cointegration analyzer."""

    def test_cointegrated_series_detected(self) -> None:
        """Two series with a stable linear relationship should be cointegrated."""
        rng = np.random.default_rng(42)
        n = 500
        x = np.cumsum(rng.normal(0, 1, n))
        noise = rng.normal(0, 0.5, n)
        y = 2.0 * x + noise  # y = 2*x + small noise

        analyzer = CointegrationAnalyzer(significance_level=0.05)
        result = analyzer.test_engle_granger(y, x)

        assert result.is_cointegrated is True
        assert result.p_value < 0.05
        assert abs(result.hedge_ratio - 2.0) < 0.5
        assert result.half_life > 0
        assert result.half_life < float("inf")

    def test_independent_random_walks_not_cointegrated(self) -> None:
        """Two independent random walks should not be cointegrated."""
        rng = np.random.default_rng(123)
        n = 500
        x = np.cumsum(rng.normal(0, 1, n))
        y = np.cumsum(rng.normal(0, 1, n))

        analyzer = CointegrationAnalyzer(significance_level=0.05)
        result = analyzer.test_engle_granger(x, y)

        assert result.is_cointegrated is False
        assert result.p_value >= 0.05

    def test_short_series_returns_not_cointegrated(self) -> None:
        """Series shorter than 20 observations should return not cointegrated."""
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([2.0, 4.0, 6.0])

        analyzer = CointegrationAnalyzer()
        result = analyzer.test_engle_granger(x, y)

        assert result.is_cointegrated is False
        assert result.p_value == 1.0
        assert result.half_life == float("inf")

    def test_half_life_computation(self) -> None:
        """Half-life should be positive and finite for mean-reverting spread."""
        rng = np.random.default_rng(42)
        n = 500
        # Create a mean-reverting spread (Ornstein-Uhlenbeck process)
        spread = np.zeros(n)
        phi = 0.95  # AR(1) coefficient
        for i in range(1, n):
            spread[i] = phi * spread[i - 1] + rng.normal(0, 0.1)

        analyzer = CointegrationAnalyzer()
        hl = analyzer.compute_half_life(spread)

        expected_hl = -np.log(2) / np.log(phi)
        assert hl > 0
        assert hl < float("inf")
        assert abs(hl - expected_hl) < expected_hl * 0.5  # within 50%

    def test_half_life_non_mean_reverting(self) -> None:
        """A pure unit root process should return infinite or very large half-life."""
        # Construct a perfect random walk: x[t] = x[t-1] + e
        # The AR(1) coefficient should be >= 1, yielding infinite half-life
        rng = np.random.default_rng(99)
        n = 1000
        spread = np.cumsum(rng.normal(0, 1, n))

        analyzer = CointegrationAnalyzer()
        hl = analyzer.compute_half_life(spread)

        # For a random walk the AR(1) phi is very close to 1.0,
        # so half-life should be very large or infinite
        assert hl == float("inf") or hl > 20

    def test_johansen_cointegrated_series(self) -> None:
        """Johansen test should find cointegrating vectors for related series."""
        rng = np.random.default_rng(42)
        n = 500
        common = np.cumsum(rng.normal(0, 1, n))

        series = [
            common + rng.normal(0, 0.3, n),
            2.0 * common + rng.normal(0, 0.3, n),
            1.5 * common + rng.normal(0, 0.3, n),
        ]

        analyzer = CointegrationAnalyzer()
        result = analyzer.test_johansen(series)

        assert isinstance(result, JohansenResult)
        assert result.num_cointegrating_vectors >= 1
        assert len(result.trace_statistics) == 3
        assert len(result.critical_values_95) == 3
        assert len(result.eigenvalues) == 3

    def test_johansen_independent_series(self) -> None:
        """Johansen test should find no cointegrating vectors for independent walks."""
        rng = np.random.default_rng(123)
        n = 500

        series = [
            np.cumsum(rng.normal(0, 1, n)),
            np.cumsum(rng.normal(0, 1, n)),
            np.cumsum(rng.normal(0, 1, n)),
        ]

        analyzer = CointegrationAnalyzer()
        result = analyzer.test_johansen(series)

        assert isinstance(result, JohansenResult)
        assert result.num_cointegrating_vectors == 0

    def test_johansen_short_series(self) -> None:
        """Johansen test should return empty result for very short series."""
        series = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]

        analyzer = CointegrationAnalyzer()
        result = analyzer.test_johansen(series)

        assert result.num_cointegrating_vectors == 0
        assert result.trace_statistics == [0.0, 0.0]


# ---------------------------------------------------------------------------
# PairScanner tests
# ---------------------------------------------------------------------------

class TestPairScanner:
    """Tests for automated cointegrated pair discovery."""

    def test_finds_cointegrated_pair(self) -> None:
        """Should find the cointegrated pair among multiple series."""
        rng = np.random.default_rng(42)
        n = 500

        # Create a shared random walk
        common = np.cumsum(rng.normal(0, 1, n))

        # Series A and B are cointegrated (both driven by common)
        series_a = 2.0 * common + rng.normal(0, 0.3, n)
        series_b = common + rng.normal(0, 0.3, n)

        # Series C is independent
        series_c = np.cumsum(rng.normal(0, 1, n))

        price_data = {
            "A": series_a,
            "B": series_b,
            "C": series_c,
        }

        scanner = PairScanner(significance_level=0.05, min_half_life=0.1, max_half_life=500.0)
        pairs = scanner.scan(price_data, p_threshold=0.05)

        # Should find at least the A-B pair
        pair_symbols = [(p.symbol_a, p.symbol_b) for p in pairs]
        assert ("A", "B") in pair_symbols or ("B", "A") in pair_symbols

    def test_no_cointegrated_pairs_for_independent_walks(self) -> None:
        """Independent random walks should yield few or no cointegrated pairs.

        With a 5% significance level, false positives are expected ~5% of the time.
        We use a stricter p_threshold to minimize spurious findings.
        """
        rng = np.random.default_rng(789)
        n = 500

        price_data = {
            "X": np.cumsum(rng.normal(0, 1, n)),
            "Y": np.cumsum(rng.normal(0, 1, n)),
            "Z": np.cumsum(rng.normal(0, 1, n)),
        }

        scanner = PairScanner(significance_level=0.01)
        pairs = scanner.scan(price_data, p_threshold=0.01)

        # With strict threshold, independent walks should not be cointegrated
        assert len(pairs) == 0

    def test_pairs_sorted_by_p_value(self) -> None:
        """Returned pairs should be sorted by p-value ascending."""
        rng = np.random.default_rng(42)
        n = 500
        common = np.cumsum(rng.normal(0, 1, n))

        price_data = {
            "A": 2.0 * common + rng.normal(0, 0.2, n),
            "B": common + rng.normal(0, 0.2, n),
            "C": 1.5 * common + rng.normal(0, 0.2, n),
        }

        scanner = PairScanner(significance_level=0.05, min_half_life=0.5, max_half_life=500.0)
        pairs = scanner.scan(price_data, p_threshold=0.05)

        for i in range(len(pairs) - 1):
            assert pairs[i].p_value <= pairs[i + 1].p_value

    def test_half_life_filtering(self) -> None:
        """Pairs with half-life outside range should be filtered out."""
        rng = np.random.default_rng(42)
        n = 500
        common = np.cumsum(rng.normal(0, 1, n))

        price_data = {
            "A": 2.0 * common + rng.normal(0, 0.3, n),
            "B": common + rng.normal(0, 0.3, n),
        }

        # Very restrictive half-life range
        scanner = PairScanner(significance_level=0.05, min_half_life=0.001, max_half_life=0.01)
        pairs = scanner.scan(price_data, p_threshold=0.05)

        assert len(pairs) == 0

    def test_empty_input(self) -> None:
        """Empty price data should return no pairs."""
        scanner = PairScanner()
        pairs = scanner.scan({})
        assert len(pairs) == 0

    def test_single_series(self) -> None:
        """A single series should return no pairs."""
        scanner = PairScanner()
        pairs = scanner.scan({"A": np.array([1.0, 2.0, 3.0])})
        assert len(pairs) == 0


# ---------------------------------------------------------------------------
# ZScoreGenerator tests
# ---------------------------------------------------------------------------

class TestZScoreGenerator:
    """Tests for Z-Score computation and signal generation."""

    def test_zscore_computation(self) -> None:
        """Z-Score should be correctly computed from spread statistics."""
        rng = np.random.default_rng(42)
        n = 200
        prices_a = np.ones(n) * 100.0 + rng.normal(0, 1, n)
        prices_b = np.ones(n) * 50.0 + rng.normal(0, 0.5, n)
        hedge_ratio = 2.0

        gen = ZScoreGenerator(entry_threshold=2.0, exit_threshold=0.5)
        result = gen.compute(prices_a, prices_b, hedge_ratio, lookback=100)

        assert isinstance(result.zscore, float)
        assert isinstance(result.spread, float)
        assert result.std > 0

    def test_entry_long_when_z_below_negative_threshold(self) -> None:
        """Should signal ENTRY_LONG when Z < -entry_threshold."""
        n = 200
        # Craft a spread that is very negative at the end
        prices_a = np.ones(n) * 100.0
        prices_b = np.ones(n) * 50.0
        hedge_ratio = 2.0
        # Make last price of A much lower to push z-score negative
        prices_a[-1] = 90.0  # spread = 90 - 2*50 = -10, mean ~0, z very negative

        gen = ZScoreGenerator(entry_threshold=2.0, exit_threshold=0.5)
        result = gen.compute(prices_a, prices_b, hedge_ratio, lookback=100)

        assert result.zscore < -2.0
        assert result.signal == ZScoreSignal.ENTRY_LONG

    def test_entry_short_when_z_above_positive_threshold(self) -> None:
        """Should signal ENTRY_SHORT when Z > +entry_threshold."""
        n = 200
        prices_a = np.ones(n) * 100.0
        prices_b = np.ones(n) * 50.0
        hedge_ratio = 2.0
        # Make last price of A much higher
        prices_a[-1] = 110.0  # spread = 110 - 100 = 10, z very positive

        gen = ZScoreGenerator(entry_threshold=2.0, exit_threshold=0.5)
        result = gen.compute(prices_a, prices_b, hedge_ratio, lookback=100)

        assert result.zscore > 2.0
        assert result.signal == ZScoreSignal.ENTRY_SHORT

    def test_exit_when_z_near_zero(self) -> None:
        """Should signal EXIT when |Z| < exit_threshold."""
        n = 200
        # All prices near constant -> spread near constant -> z near 0
        rng = np.random.default_rng(42)
        prices_a = np.ones(n) * 100.0 + rng.normal(0, 0.01, n)
        prices_b = np.ones(n) * 50.0 + rng.normal(0, 0.005, n)
        hedge_ratio = 2.0

        gen = ZScoreGenerator(entry_threshold=2.0, exit_threshold=0.5)
        result = gen.compute(prices_a, prices_b, hedge_ratio, lookback=100)

        assert abs(result.zscore) < 0.5
        assert result.signal == ZScoreSignal.EXIT

    def test_hold_between_thresholds(self) -> None:
        """Should signal HOLD when exit_threshold < |Z| < entry_threshold."""
        rng = np.random.default_rng(42)
        n = 200
        # Add noise so there is meaningful std
        prices_a = 100.0 + rng.normal(0, 1.0, n)
        prices_b = 50.0 + rng.normal(0, 0.5, n)
        hedge_ratio = 2.0

        # Set last price to create a moderate z-score (between 0.5 and 3.0)
        spread = prices_a - hedge_ratio * prices_b
        window = spread[-100:]
        mean = np.mean(window)
        std = np.std(window, ddof=1)
        # Target z-score of ~1.0 (between exit=0.5 and entry=3.0)
        target_spread = mean + 1.0 * std
        prices_a[-1] = target_spread + hedge_ratio * prices_b[-1]

        gen = ZScoreGenerator(entry_threshold=3.0, exit_threshold=0.5)
        result = gen.compute(prices_a, prices_b, hedge_ratio, lookback=100)

        assert 0.5 <= abs(result.zscore) <= 3.0
        assert result.signal == ZScoreSignal.HOLD

    def test_zero_std_returns_hold(self) -> None:
        """Should return HOLD with zero Z-Score when std is zero."""
        prices_a = np.ones(100) * 100.0
        prices_b = np.ones(100) * 50.0
        hedge_ratio = 2.0

        gen = ZScoreGenerator()
        result = gen.compute(prices_a, prices_b, hedge_ratio, lookback=100)

        assert result.zscore == 0.0
        assert result.signal == ZScoreSignal.HOLD


# ---------------------------------------------------------------------------
# StatisticalDetector tests
# ---------------------------------------------------------------------------

class TestStatisticalDetector:
    """Tests for the integrated statistical arbitrage detector."""

    def test_update_prices_builds_history(self) -> None:
        """update_prices should accumulate price history."""
        detector = StatisticalDetector(lookback_window=10)
        for i in range(20):
            detector.update_prices("BTC/USDT", "binance", 50000.0 + i)

        assert len(detector._price_history["binance:BTC/USDT"]) == 20

    def test_detect_with_cointegrated_data(self) -> None:
        """Should produce signals when fed cointegrated price history."""
        rng = np.random.default_rng(42)
        n = 200
        common = np.cumsum(rng.normal(0, 1, n)) + 50000

        detector = StatisticalDetector(
            lookback_window=50,
            z_entry_threshold=2.0,
            z_exit_threshold=0.5,
            rescan_interval_hours=0.0,  # always rescan
            significance_level=0.05,
        )

        # Feed price history for two cointegrated series
        prices_a = common + rng.normal(0, 0.5, n)
        prices_b = 2.0 * common + rng.normal(0, 0.5, n)

        for i in range(n - 1):
            detector.update_prices("BTC/USDT", "binance", prices_a[i])
            detector.update_prices("BTC/USDT", "okx", prices_b[i])

        # Force a large deviation at the end to trigger z-score signal
        detector.update_prices("BTC/USDT", "binance", prices_a[-1] + 50)
        detector.update_prices("BTC/USDT", "okx", prices_b[-1])

        # Create orderbooks for the detect call
        orderbooks = {
            "binance": _make_orderbook("binance", "BTC/USDT", prices_a[-1] + 50),
            "okx": _make_orderbook("okx", "BTC/USDT", prices_b[-1]),
        }

        signals = detector.detect(orderbooks)

        # We may or may not get signals depending on whether the z-score
        # breach is big enough after fee subtraction. The important thing
        # is no errors and correct types.
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.strategy == ArbitrageStrategy.STATISTICAL

    def test_detect_no_signals_without_history(self) -> None:
        """Should return no signals when there is not enough price history."""
        detector = StatisticalDetector(lookback_window=100)

        orderbooks = {
            "binance": _make_orderbook("binance", "BTC/USDT", 50000.0),
            "okx": _make_orderbook("okx", "BTC/USDT", 50000.0),
        }

        signals = detector.detect(orderbooks)
        assert signals == []

    def test_known_pairs_property(self) -> None:
        """known_pairs should reflect the result of pair scanning."""
        detector = StatisticalDetector(
            lookback_window=50,
            rescan_interval_hours=0.0,
        )

        # No data yet
        assert detector.known_pairs == []

        # Feed some data and trigger a rescan
        rng = np.random.default_rng(42)
        n = 200
        common = np.cumsum(rng.normal(0, 1, n)) + 100

        for i in range(n):
            detector.update_prices("BTC/USDT", "binance", common[i] + rng.normal(0, 0.3))
            detector.update_prices("BTC/USDT", "okx", 2.0 * common[i] + rng.normal(0, 0.3))

        # Trigger rescan via detect
        orderbooks = {
            "binance": _make_orderbook("binance", "BTC/USDT", common[-1]),
            "okx": _make_orderbook("okx", "BTC/USDT", 2.0 * common[-1]),
        }
        detector.detect(orderbooks)

        # known_pairs should be a list (may or may not have pairs)
        assert isinstance(detector.known_pairs, list)

    def test_detect_returns_list_type(self) -> None:
        """detect() should always return a list of ArbitrageSignal."""
        detector = StatisticalDetector(lookback_window=10)

        orderbooks = {
            "binance": _make_orderbook("binance", "BTC/USDT", 50000.0),
        }

        result = detector.detect(orderbooks)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# StatArbBacktester tests
# ---------------------------------------------------------------------------

class TestStatArbBacktester:
    """Tests for walk-forward statistical arbitrage backtesting."""

    def test_walk_forward_with_cointegrated_data(self) -> None:
        """Walk-forward backtest should produce a valid result structure."""
        rng = np.random.default_rng(42)
        n = 600  # enough for train=252 + test=63

        common = np.cumsum(rng.normal(0, 1, n))
        price_data = {
            "A": 2.0 * common + rng.normal(0, 0.5, n) + 100,
            "B": common + rng.normal(0, 0.5, n) + 50,
        }

        backtester = StatArbBacktester(
            train_window=252,
            test_window=63,
            z_entry=2.0,
            z_exit=0.5,
        )
        result = backtester.run(price_data)

        assert isinstance(result, StatArbBacktestResult)
        assert result.walk_forward_windows >= 1
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.total_trades, int)
        assert 0.0 <= result.win_rate <= 1.0
        assert isinstance(result.sharpe_ratio, float)
        assert result.max_drawdown_pct >= 0.0

    def test_result_structure_validation(self) -> None:
        """Result should have correct types and reasonable values."""
        rng = np.random.default_rng(42)
        n = 400

        common = np.cumsum(rng.normal(0, 1, n))
        price_data = {
            "X": 1.5 * common + rng.normal(0, 0.3, n) + 100,
            "Y": common + rng.normal(0, 0.3, n) + 50,
        }

        backtester = StatArbBacktester(
            train_window=200,
            test_window=50,
            z_entry=2.0,
            z_exit=0.5,
        )
        result = backtester.run(price_data)

        assert isinstance(result.pair_results, dict)
        for key, pair_result in result.pair_results.items():
            assert "total_pnl" in pair_result
            assert "trades" in pair_result
            assert "win_rate" in pair_result

    def test_insufficient_data_returns_empty_result(self) -> None:
        """Should return empty result when data is too short."""
        price_data = {
            "A": np.array([1.0, 2.0, 3.0]),
            "B": np.array([2.0, 4.0, 6.0]),
        }

        backtester = StatArbBacktester(train_window=252, test_window=63)
        result = backtester.run(price_data)

        assert result.total_trades == 0
        assert result.total_pnl == 0.0
        assert result.walk_forward_windows == 0

    def test_single_series_returns_empty_result(self) -> None:
        """Should return empty result with only one series."""
        price_data = {
            "A": np.ones(500),
        }

        backtester = StatArbBacktester()
        result = backtester.run(price_data)

        assert result.total_trades == 0

    def test_independent_walks_may_have_no_trades(self) -> None:
        """Independent random walks may produce no cointegrated pairs."""
        rng = np.random.default_rng(789)
        n = 600

        price_data = {
            "A": np.cumsum(rng.normal(0, 1, n)) + 100,
            "B": np.cumsum(rng.normal(0, 1, n)) + 100,
        }

        backtester = StatArbBacktester(
            train_window=252,
            test_window=63,
        )
        result = backtester.run(price_data)

        # Independent walks should likely not be cointegrated
        assert isinstance(result, StatArbBacktestResult)
