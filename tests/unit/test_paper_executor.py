"""Unit tests for the paper trading executor and fill simulator."""

import pytest

from arbot.execution.base import InsufficientBalanceError
from arbot.execution.fill_simulator import FillSimulator
from arbot.execution.paper_executor import PaperExecutor
from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.models.signal import ArbitrageSignal, ArbitrageStrategy
from arbot.models.trade import OrderSide, OrderStatus


def _make_ob(
    exchange: str,
    symbol: str,
    best_bid: float,
    best_ask: float,
    depth_qty: float = 10.0,
) -> OrderBook:
    """Create a simple OrderBook with 3 levels per side."""
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
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
def fee() -> TradingFee:
    return TradingFee(maker_pct=0.02, taker_pct=0.1)


@pytest.fixture
def signal() -> ArbitrageSignal:
    return ArbitrageSignal(
        strategy=ArbitrageStrategy.SPATIAL,
        buy_exchange="binance",
        sell_exchange="upbit",
        symbol="BTC/USDT",
        buy_price=50000.0,
        sell_price=50300.0,
        quantity=0.1,
        gross_spread_pct=0.6,
        net_spread_pct=0.5,
        estimated_profit_usd=25.0,
        confidence=0.8,
        orderbook_depth_usd=100000.0,
    )


# ---------------------------------------------------------------------------
# FillSimulator tests
# ---------------------------------------------------------------------------


class TestFillSimulator:
    """Tests for FillSimulator."""

    def test_buy_fill_single_level(self, fee: TradingFee) -> None:
        ob = _make_ob("binance", "BTC/USDT", best_bid=49900, best_ask=50000)
        result = FillSimulator.simulate_fill(ob, OrderSide.BUY, 1.0, fee)

        assert result.order.side == OrderSide.BUY
        assert result.order.exchange == "binance"
        assert result.order.symbol == "BTC/USDT"
        assert result.order.status == OrderStatus.FILLED
        assert result.filled_quantity == pytest.approx(1.0)
        assert result.filled_price == pytest.approx(50000.0)
        assert result.fee_asset == "BTC"
        assert result.fee == pytest.approx(1.0 * 0.1 / 100)
        assert result.latency_ms >= 0

    def test_sell_fill_single_level(self, fee: TradingFee) -> None:
        ob = _make_ob("upbit", "BTC/USDT", best_bid=50300, best_ask=50400)
        result = FillSimulator.simulate_fill(ob, OrderSide.SELL, 1.0, fee)

        assert result.order.side == OrderSide.SELL
        assert result.order.status == OrderStatus.FILLED
        assert result.filled_quantity == pytest.approx(1.0)
        assert result.filled_price == pytest.approx(50300.0)
        assert result.fee_asset == "USDT"
        # fee = cost * taker_pct/100 = 50300 * 0.001 = 50.3
        assert result.fee == pytest.approx(50300.0 * 0.001)

    def test_fill_multiple_levels(self, fee: TradingFee) -> None:
        ob = _make_ob("binance", "BTC/USDT", best_bid=49900, best_ask=50000, depth_qty=5.0)
        # Request 8 BTC: level1=5@50000, level2=3@50010
        result = FillSimulator.simulate_fill(ob, OrderSide.BUY, 8.0, fee)

        assert result.filled_quantity == pytest.approx(8.0)
        expected_vwap = (5.0 * 50000 + 3.0 * 50010) / 8.0
        assert result.filled_price == pytest.approx(expected_vwap)
        assert result.order.status == OrderStatus.FILLED

    def test_partial_fill(self, fee: TradingFee) -> None:
        ob = _make_ob("binance", "BTC/USDT", best_bid=49900, best_ask=50000, depth_qty=5.0)
        # Request 100 BTC but only 15 available (5+5+5)
        result = FillSimulator.simulate_fill(ob, OrderSide.BUY, 100.0, fee)

        assert result.filled_quantity == pytest.approx(15.0)
        assert result.order.status == OrderStatus.PARTIAL

    def test_empty_orderbook_fails(self, fee: TradingFee) -> None:
        ob = OrderBook(exchange="test", symbol="X/Y", timestamp=0.0)
        result = FillSimulator.simulate_fill(ob, OrderSide.BUY, 1.0, fee)

        assert result.filled_quantity == 0.0
        assert result.order.status == OrderStatus.FAILED


# ---------------------------------------------------------------------------
# PaperExecutor - basic execution
# ---------------------------------------------------------------------------


class TestPaperExecutorExecution:
    """Tests for PaperExecutor trade execution."""

    def test_execute_updates_balances(
        self, signal: ArbitrageSignal, fee: TradingFee
    ) -> None:
        initial = {
            "binance": {"USDT": 100000.0, "BTC": 0.0},
            "upbit": {"USDT": 0.0, "BTC": 1.0},
        }
        fees = {"binance": fee, "upbit": fee}
        executor = PaperExecutor(initial_balances=initial, exchange_fees=fees)

        buy_ob = _make_ob("binance", "BTC/USDT", best_bid=49900, best_ask=50000)
        sell_ob = _make_ob("upbit", "BTC/USDT", best_bid=50300, best_ask=50400)
        executor.update_orderbooks({
            "binance:BTC/USDT": buy_ob,
            "upbit:BTC/USDT": sell_ob,
        })

        buy_result, sell_result = executor.execute(signal)

        # Verify fills
        assert buy_result.filled_quantity == pytest.approx(0.1)
        assert sell_result.filled_quantity == pytest.approx(0.1)

        # Verify balance changes
        # Buy side: spent USDT, gained BTC (minus fee)
        binance_usdt = executor.balances["binance"]["USDT"]
        assert binance_usdt < 100000.0  # spent USDT

        binance_btc = executor.balances["binance"]["BTC"]
        assert binance_btc > 0.0  # gained BTC

        # Sell side: spent BTC, gained USDT (minus fee)
        upbit_btc = executor.balances["upbit"]["BTC"]
        assert upbit_btc < 1.0  # sold BTC

        upbit_usdt = executor.balances["upbit"]["USDT"]
        assert upbit_usdt > 0.0  # gained USDT

    def test_trade_history_recorded(
        self, signal: ArbitrageSignal, fee: TradingFee
    ) -> None:
        initial = {
            "binance": {"USDT": 100000.0, "BTC": 0.0},
            "upbit": {"USDT": 0.0, "BTC": 1.0},
        }
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee, "upbit": fee},
        )
        executor.update_orderbooks({
            "binance:BTC/USDT": _make_ob("binance", "BTC/USDT", 49900, 50000),
            "upbit:BTC/USDT": _make_ob("upbit", "BTC/USDT", 50300, 50400),
        })

        assert len(executor.get_trade_history()) == 0
        executor.execute(signal)
        assert len(executor.get_trade_history()) == 1

        buy_r, sell_r = executor.get_trade_history()[0]
        assert buy_r.order.side == OrderSide.BUY
        assert sell_r.order.side == OrderSide.SELL


# ---------------------------------------------------------------------------
# PaperExecutor - insufficient balance
# ---------------------------------------------------------------------------


class TestPaperExecutorInsufficientBalance:
    """Tests for balance checking."""

    def test_insufficient_quote_for_buy(
        self, signal: ArbitrageSignal, fee: TradingFee
    ) -> None:
        initial = {
            "binance": {"USDT": 1.0},  # Not enough USDT to buy 0.1 BTC
            "upbit": {"BTC": 1.0},
        }
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee, "upbit": fee},
        )
        executor.update_orderbooks({
            "binance:BTC/USDT": _make_ob("binance", "BTC/USDT", 49900, 50000),
            "upbit:BTC/USDT": _make_ob("upbit", "BTC/USDT", 50300, 50400),
        })

        with pytest.raises(InsufficientBalanceError) as exc_info:
            executor.execute(signal)
        assert exc_info.value.exchange == "binance"
        assert exc_info.value.asset == "USDT"

    def test_insufficient_base_for_sell_scales_down(
        self, signal: ArbitrageSignal, fee: TradingFee
    ) -> None:
        initial = {
            "binance": {"USDT": 100000.0},
            "upbit": {"BTC": 0.001},  # Less than 0.1 BTC, but enough for min trade
        }
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee, "upbit": fee},
        )
        executor.update_orderbooks({
            "binance:BTC/USDT": _make_ob("binance", "BTC/USDT", 49900, 50000),
            "upbit:BTC/USDT": _make_ob("upbit", "BTC/USDT", 50300, 50400),
        })

        # Should scale down to 0.001 BTC instead of raising error
        buy_result, sell_result = executor.execute(signal)
        assert buy_result.filled_quantity <= 0.001
        assert sell_result.filled_quantity <= 0.001

    def test_insufficient_balance_below_minimum(
        self, signal: ArbitrageSignal, fee: TradingFee
    ) -> None:
        initial = {
            "binance": {"USDT": 1.0},  # Below $10 minimum
            "upbit": {"BTC": 0.0000001},  # Below $10 minimum
        }
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee, "upbit": fee},
        )
        executor.update_orderbooks({
            "binance:BTC/USDT": _make_ob("binance", "BTC/USDT", 49900, 50000),
            "upbit:BTC/USDT": _make_ob("upbit", "BTC/USDT", 50300, 50400),
        })

        with pytest.raises(InsufficientBalanceError):
            executor.execute(signal)


# ---------------------------------------------------------------------------
# PaperExecutor - PnL
# ---------------------------------------------------------------------------


class TestPaperExecutorPnL:
    """Tests for PnL calculation."""

    def test_pnl_after_profitable_trade(
        self, signal: ArbitrageSignal, fee: TradingFee
    ) -> None:
        initial = {
            "binance": {"USDT": 100000.0, "BTC": 0.0},
            "upbit": {"USDT": 0.0, "BTC": 1.0},
        }
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee, "upbit": fee},
        )
        executor.update_orderbooks({
            "binance:BTC/USDT": _make_ob("binance", "BTC/USDT", 49900, 50000),
            "upbit:BTC/USDT": _make_ob("upbit", "BTC/USDT", 50300, 50400),
        })

        executor.execute(signal)
        pnl = executor.get_pnl()

        # There should be PnL entries
        assert len(pnl) > 0

        # Binance: spent USDT, gained BTC => USDT negative, BTC positive
        assert pnl["binance"]["USDT"] < 0
        assert pnl["binance"]["BTC"] > 0

        # Upbit: spent BTC, gained USDT => BTC negative, USDT positive
        assert pnl["upbit"]["BTC"] < 0
        assert pnl["upbit"]["USDT"] > 0

    def test_pnl_zero_before_trades(self, fee: TradingFee) -> None:
        initial = {"binance": {"USDT": 10000.0}}
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee},
        )
        pnl = executor.get_pnl()
        assert len(pnl) == 0


# ---------------------------------------------------------------------------
# PaperExecutor - portfolio snapshot
# ---------------------------------------------------------------------------


class TestPaperExecutorPortfolio:
    """Tests for portfolio snapshot."""

    def test_get_portfolio_reflects_initial(self, fee: TradingFee) -> None:
        initial = {
            "binance": {"USDT": 50000.0, "BTC": 0.5},
            "upbit": {"USDT": 30000.0},
        }
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee, "upbit": fee},
        )
        snapshot = executor.get_portfolio()

        assert "binance" in snapshot.exchange_balances
        assert "upbit" in snapshot.exchange_balances

        binance_bal = snapshot.exchange_balances["binance"]
        assert binance_bal.balances["USDT"].free == pytest.approx(50000.0)
        assert binance_bal.balances["BTC"].free == pytest.approx(0.5)

    def test_portfolio_updates_after_trade(
        self, signal: ArbitrageSignal, fee: TradingFee
    ) -> None:
        initial = {
            "binance": {"USDT": 100000.0, "BTC": 0.0},
            "upbit": {"USDT": 0.0, "BTC": 1.0},
        }
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee, "upbit": fee},
        )
        executor.update_orderbooks({
            "binance:BTC/USDT": _make_ob("binance", "BTC/USDT", 49900, 50000),
            "upbit:BTC/USDT": _make_ob("upbit", "BTC/USDT", 50300, 50400),
        })

        before = executor.get_portfolio()
        executor.execute(signal)
        after = executor.get_portfolio()

        # Binance USDT should decrease after buying BTC
        before_usdt = before.exchange_balances["binance"].balances["USDT"].free
        after_usdt = after.exchange_balances["binance"].balances["USDT"].free
        assert after_usdt < before_usdt

    def test_missing_orderbook_raises(
        self, signal: ArbitrageSignal, fee: TradingFee
    ) -> None:
        initial = {
            "binance": {"USDT": 100000.0},
            "upbit": {"BTC": 1.0},
        }
        executor = PaperExecutor(
            initial_balances=initial,
            exchange_fees={"binance": fee, "upbit": fee},
        )
        # No orderbooks updated

        with pytest.raises(ValueError, match="Missing orderbook"):
            executor.execute(signal)
