"""Unit tests for the spread calculator module."""

import pytest

from arbot.detector.spread_calculator import ArbitrageProfit, SpreadCalculator
from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.models.trade import OrderSide


@pytest.fixture
def calc() -> SpreadCalculator:
    return SpreadCalculator()


@pytest.fixture
def buy_orderbook() -> OrderBook:
    """Order book for the exchange to BUY on (lower prices)."""
    return OrderBook(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=1700000000.0,
        bids=[
            OrderBookEntry(price=49950.0, quantity=1.0),
            OrderBookEntry(price=49900.0, quantity=2.0),
        ],
        asks=[
            OrderBookEntry(price=50000.0, quantity=1.0),
            OrderBookEntry(price=50100.0, quantity=2.0),
            OrderBookEntry(price=50200.0, quantity=3.0),
        ],
    )


@pytest.fixture
def sell_orderbook() -> OrderBook:
    """Order book for the exchange to SELL on (higher prices)."""
    return OrderBook(
        exchange="upbit",
        symbol="BTC/USDT",
        timestamp=1700000000.0,
        bids=[
            OrderBookEntry(price=50300.0, quantity=1.0),
            OrderBookEntry(price=50200.0, quantity=2.0),
            OrderBookEntry(price=50100.0, quantity=3.0),
        ],
        asks=[
            OrderBookEntry(price=50400.0, quantity=1.0),
            OrderBookEntry(price=50500.0, quantity=2.0),
        ],
    )


@pytest.fixture
def low_fee() -> TradingFee:
    return TradingFee(maker_pct=0.02, taker_pct=0.04)


@pytest.fixture
def high_fee() -> TradingFee:
    return TradingFee(maker_pct=0.1, taker_pct=0.15)


# ---------------------------------------------------------------------------
# Gross spread
# ---------------------------------------------------------------------------


class TestGrossSpread:
    """Tests for calculate_gross_spread."""

    def test_positive_spread(self, calc: SpreadCalculator) -> None:
        result = calc.calculate_gross_spread(50000.0, 50500.0)
        assert result == pytest.approx(1.0)

    def test_zero_spread(self, calc: SpreadCalculator) -> None:
        result = calc.calculate_gross_spread(50000.0, 50000.0)
        assert result == pytest.approx(0.0)

    def test_negative_spread(self, calc: SpreadCalculator) -> None:
        result = calc.calculate_gross_spread(50000.0, 49500.0)
        assert result == pytest.approx(-1.0)

    def test_zero_buy_price(self, calc: SpreadCalculator) -> None:
        result = calc.calculate_gross_spread(0.0, 50000.0)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Net spread (with fees)
# ---------------------------------------------------------------------------


class TestNetSpread:
    """Tests for calculate_net_spread."""

    def test_net_spread_deducts_fees(self, calc: SpreadCalculator) -> None:
        # gross spread = 1.0%, fees = 0.1 + 0.1 = 0.2%
        result = calc.calculate_net_spread(50000.0, 50500.0, 0.1, 0.1)
        assert result == pytest.approx(0.8)

    def test_net_spread_becomes_negative(self, calc: SpreadCalculator) -> None:
        # gross spread = 0.1%, fees = 0.1 + 0.1 = 0.2%, net = -0.1%
        result = calc.calculate_net_spread(50000.0, 50050.0, 0.1, 0.1)
        assert result == pytest.approx(-0.1)

    def test_net_spread_with_zero_fees(self, calc: SpreadCalculator) -> None:
        result = calc.calculate_net_spread(50000.0, 50500.0, 0.0, 0.0)
        assert result == pytest.approx(1.0)

    def test_net_spread_zero_buy_price(self, calc: SpreadCalculator) -> None:
        result = calc.calculate_net_spread(0.0, 50000.0, 0.1, 0.1)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Effective price (order book VWAP)
# ---------------------------------------------------------------------------


class TestEffectivePrice:
    """Tests for calculate_effective_price."""

    def test_buy_single_level(
        self, calc: SpreadCalculator, buy_orderbook: OrderBook
    ) -> None:
        # First ask: 50000 * 1.0 = 50000 USD, request exactly 50000
        price = calc.calculate_effective_price(buy_orderbook, OrderSide.BUY, 50000.0)
        assert price == pytest.approx(50000.0)

    def test_buy_multiple_levels(
        self, calc: SpreadCalculator, buy_orderbook: OrderBook
    ) -> None:
        # Ask levels: 50000*1=50000, 50100*2=100200
        # Request 100000 USD:
        #   level1: full 1.0 BTC at 50000 = 50000 USD
        #   level2: partial (100000-50000)/50100 BTC at 50100 = 50000 USD
        # total_cost = 100000, total_qty = 1.0 + 50000/50100
        price = calc.calculate_effective_price(buy_orderbook, OrderSide.BUY, 100000.0)
        total_qty = 1.0 + 50000.0 / 50100.0
        expected = 100000.0 / total_qty
        assert price == pytest.approx(expected)

    def test_sell_single_level(
        self, calc: SpreadCalculator, sell_orderbook: OrderBook
    ) -> None:
        # First bid: 50300 * 1.0 = 50300 USD
        price = calc.calculate_effective_price(sell_orderbook, OrderSide.SELL, 50300.0)
        assert price == pytest.approx(50300.0)

    def test_sell_multiple_levels(
        self, calc: SpreadCalculator, sell_orderbook: OrderBook
    ) -> None:
        # Bid levels: 50300*1=50300, 50200*2=100400
        # Request 100000 USD:
        #   level1: full 1.0 BTC at 50300 = 50300 USD
        #   level2: partial (100000-50300)/50200 BTC at 50200 = 49700 USD
        # total_cost = 100000, total_qty = 1.0 + 49700/50200
        price = calc.calculate_effective_price(sell_orderbook, OrderSide.SELL, 100000.0)
        total_qty = 1.0 + 49700.0 / 50200.0
        expected = 100000.0 / total_qty
        assert price == pytest.approx(expected)

    def test_zero_quantity(
        self, calc: SpreadCalculator, buy_orderbook: OrderBook
    ) -> None:
        price = calc.calculate_effective_price(buy_orderbook, OrderSide.BUY, 0.0)
        assert price == 0.0

    def test_empty_orderbook(self, calc: SpreadCalculator) -> None:
        ob = OrderBook(exchange="test", symbol="X/Y", timestamp=0.0)
        price = calc.calculate_effective_price(ob, OrderSide.BUY, 1000.0)
        assert price == 0.0


# ---------------------------------------------------------------------------
# Arbitrage profit
# ---------------------------------------------------------------------------


class TestArbitrageProfit:
    """Tests for calculate_arbitrage_profit."""

    def test_profitable_opportunity(
        self,
        calc: SpreadCalculator,
        buy_orderbook: OrderBook,
        sell_orderbook: OrderBook,
        low_fee: TradingFee,
    ) -> None:
        profit = calc.calculate_arbitrage_profit(
            buy_orderbook, sell_orderbook, low_fee, low_fee, 50000.0
        )
        # buy at best ask 50000, sell at best bid 50300
        assert profit.buy_effective_price == pytest.approx(50000.0)
        assert profit.sell_effective_price == pytest.approx(50300.0)
        # gross = (50300-50000)/50000 * 100 = 0.6%
        assert profit.gross_spread_pct == pytest.approx(0.6)
        # net = 0.6 - 0.02(buy maker) - 0.04(sell taker) = 0.54%
        assert profit.net_spread_pct == pytest.approx(0.54)
        # profit = 0.54/100 * 50000 = 270
        assert profit.estimated_profit_usd == pytest.approx(270.0)
        assert profit.available_depth_usd > 0
        assert profit.is_profitable is True

    def test_profit_with_depth(
        self,
        calc: SpreadCalculator,
        buy_orderbook: OrderBook,
        sell_orderbook: OrderBook,
        low_fee: TradingFee,
    ) -> None:
        # Larger quantity spans multiple levels, effective prices worsen
        profit_small = calc.calculate_arbitrage_profit(
            buy_orderbook, sell_orderbook, low_fee, low_fee, 50000.0
        )
        profit_large = calc.calculate_arbitrage_profit(
            buy_orderbook, sell_orderbook, low_fee, low_fee, 100000.0
        )
        # Larger trade => higher buy price, lower sell price => worse spread
        assert profit_large.buy_effective_price > profit_small.buy_effective_price
        assert profit_large.sell_effective_price < profit_small.sell_effective_price
        assert profit_large.gross_spread_pct < profit_small.gross_spread_pct

    def test_high_fees_reduce_profit(
        self,
        calc: SpreadCalculator,
        buy_orderbook: OrderBook,
        sell_orderbook: OrderBook,
        low_fee: TradingFee,
        high_fee: TradingFee,
    ) -> None:
        profit_low = calc.calculate_arbitrage_profit(
            buy_orderbook, sell_orderbook, low_fee, low_fee, 50000.0
        )
        profit_high = calc.calculate_arbitrage_profit(
            buy_orderbook, sell_orderbook, high_fee, high_fee, 50000.0
        )
        assert profit_high.net_spread_pct < profit_low.net_spread_pct
        assert profit_high.estimated_profit_usd < profit_low.estimated_profit_usd

    def test_available_depth(
        self,
        calc: SpreadCalculator,
        buy_orderbook: OrderBook,
        sell_orderbook: OrderBook,
        low_fee: TradingFee,
    ) -> None:
        profit = calc.calculate_arbitrage_profit(
            buy_orderbook, sell_orderbook, low_fee, low_fee, 50000.0
        )
        # buy asks: 50000*1 + 50100*2 + 50200*3 = 50000+100200+150600 = 300800
        # sell bids: 50300*1 + 50200*2 + 50100*3 = 50300+100400+150300 = 301000
        assert profit.available_depth_usd == pytest.approx(300800.0)


# ---------------------------------------------------------------------------
# Profitability check
# ---------------------------------------------------------------------------


class TestIsProfitable:
    """Tests for is_profitable field and static method."""

    def test_profitable(self, calc: SpreadCalculator) -> None:
        profit = ArbitrageProfit(
            buy_effective_price=50000.0,
            sell_effective_price=50300.0,
            gross_spread_pct=0.6,
            net_spread_pct=0.5,
            estimated_profit_usd=250.0,
            available_depth_usd=100000.0,
            is_profitable=True,
        )
        assert profit.is_profitable is True
        assert calc.is_profitable(profit, min_spread_pct=0.25) is True

    def test_not_profitable_spread_too_low(self, calc: SpreadCalculator) -> None:
        profit = ArbitrageProfit(
            buy_effective_price=50000.0,
            sell_effective_price=50050.0,
            gross_spread_pct=0.1,
            net_spread_pct=0.05,
            estimated_profit_usd=25.0,
            available_depth_usd=100000.0,
            is_profitable=True,
        )
        # is_profitable field is True (positive spread/profit), but
        # static method checks against min_spread_pct threshold
        assert calc.is_profitable(profit, min_spread_pct=0.25) is False

    def test_not_profitable_negative_profit(self, calc: SpreadCalculator) -> None:
        profit = ArbitrageProfit(
            buy_effective_price=50000.0,
            sell_effective_price=49900.0,
            gross_spread_pct=-0.2,
            net_spread_pct=-0.4,
            estimated_profit_usd=-200.0,
            available_depth_usd=100000.0,
            is_profitable=False,
        )
        assert profit.is_profitable is False
        assert calc.is_profitable(profit, min_spread_pct=0.25) is False

    def test_exactly_at_threshold(self, calc: SpreadCalculator) -> None:
        profit = ArbitrageProfit(
            buy_effective_price=50000.0,
            sell_effective_price=50125.0,
            gross_spread_pct=0.35,
            net_spread_pct=0.25,
            estimated_profit_usd=125.0,
            available_depth_usd=100000.0,
            is_profitable=True,
        )
        assert profit.is_profitable is True
        assert calc.is_profitable(profit, min_spread_pct=0.25) is True

    def test_is_profitable_set_by_calculate(
        self,
        calc: SpreadCalculator,
        buy_orderbook: OrderBook,
        sell_orderbook: OrderBook,
        high_fee: TradingFee,
    ) -> None:
        """Verify is_profitable is correctly set by calculate_arbitrage_profit."""
        profit = calc.calculate_arbitrage_profit(
            buy_orderbook, sell_orderbook, high_fee, high_fee, 50000.0
        )
        # net = 0.6 - 0.15 - 0.15 = 0.3%, profit > 0 => is_profitable = True
        assert profit.is_profitable is True
