"""Unit tests for core data models."""

import pytest

from arbot.models import (
    ArbitrageSignal,
    ArbitrageStrategy,
    AssetBalance,
    ExchangeBalance,
    OrderBook,
    OrderBookEntry,
    PortfolioSnapshot,
    RiskConfig,
    SignalStatus,
    TradingFee,
)


# ---------------------------------------------------------------------------
# OrderBook tests
# ---------------------------------------------------------------------------


class TestOrderBook:
    """Tests for OrderBook model and its properties."""

    def _make_orderbook(self) -> OrderBook:
        return OrderBook(
            exchange="binance",
            symbol="BTC/USDT",
            timestamp=1700000000.0,
            bids=[
                OrderBookEntry(price=50000.0, quantity=1.0),
                OrderBookEntry(price=49900.0, quantity=2.0),
                OrderBookEntry(price=49800.0, quantity=3.0),
            ],
            asks=[
                OrderBookEntry(price=50100.0, quantity=1.0),
                OrderBookEntry(price=50200.0, quantity=2.0),
                OrderBookEntry(price=50300.0, quantity=3.0),
            ],
        )

    def test_best_bid(self) -> None:
        ob = self._make_orderbook()
        assert ob.best_bid == 50000.0

    def test_best_ask(self) -> None:
        ob = self._make_orderbook()
        assert ob.best_ask == 50100.0

    def test_mid_price(self) -> None:
        ob = self._make_orderbook()
        assert ob.mid_price == pytest.approx(50050.0)

    def test_spread(self) -> None:
        ob = self._make_orderbook()
        assert ob.spread == pytest.approx(100.0)

    def test_spread_pct(self) -> None:
        ob = self._make_orderbook()
        expected = (100.0 / 50050.0) * 100
        assert ob.spread_pct == pytest.approx(expected)

    def test_empty_orderbook(self) -> None:
        ob = OrderBook(exchange="test", symbol="X/Y", timestamp=0.0)
        assert ob.best_bid == 0.0
        assert ob.best_ask == 0.0
        assert ob.mid_price == 0.0
        assert ob.spread == 0.0
        assert ob.spread_pct == 0.0

    def test_depth_at_price_ask(self) -> None:
        ob = self._make_orderbook()
        # First ask level: 50100 * 1.0 = 50100 USD
        # Requesting 50100 USD should give VWAP = 50100.0
        vwap = ob.depth_at_price("ask", 50100.0)
        assert vwap == pytest.approx(50100.0)

    def test_depth_at_price_bid(self) -> None:
        ob = self._make_orderbook()
        # First bid level: 50000 * 1.0 = 50000 USD
        vwap = ob.depth_at_price("bid", 50000.0)
        assert vwap == pytest.approx(50000.0)

    def test_depth_at_price_multiple_levels(self) -> None:
        ob = self._make_orderbook()
        # Ask: level1=50100*1=50100, level2=50200*2=100400, total=150500
        # Request 100000 USD:
        #   level1: qty=1.0, cost=50100 (remaining=49900)
        #   level2: partial_qty=49900/50200 ~= 0.9940..., cost=49900
        # total_qty = 1.0 + 49900/50200, total_cost = 100000
        vwap = ob.depth_at_price("ask", 100000.0)
        total_qty = 1.0 + 49900.0 / 50200.0
        expected_vwap = 100000.0 / total_qty
        assert vwap == pytest.approx(expected_vwap)

    def test_depth_at_price_zero_depth(self) -> None:
        ob = self._make_orderbook()
        assert ob.depth_at_price("ask", 0.0) == 0.0
        assert ob.depth_at_price("bid", -100.0) == 0.0

    def test_depth_at_price_empty_book(self) -> None:
        ob = OrderBook(exchange="test", symbol="X/Y", timestamp=0.0)
        assert ob.depth_at_price("ask", 1000.0) == 0.0


# ---------------------------------------------------------------------------
# ArbitrageSignal tests
# ---------------------------------------------------------------------------


class TestArbitrageSignal:
    """Tests for ArbitrageSignal model."""

    def test_create_signal(self) -> None:
        signal = ArbitrageSignal(
            strategy=ArbitrageStrategy.SPATIAL,
            buy_exchange="binance",
            sell_exchange="upbit",
            symbol="BTC/USDT",
            buy_price=50000.0,
            sell_price=50500.0,
            quantity=0.1,
            gross_spread_pct=1.0,
            net_spread_pct=0.7,
            estimated_profit_usd=35.0,
            confidence=0.85,
            orderbook_depth_usd=5000.0,
        )
        assert signal.strategy == ArbitrageStrategy.SPATIAL
        assert signal.status == SignalStatus.DETECTED
        assert signal.id is not None
        assert signal.detected_at is not None
        assert signal.executed_at is None

    def test_signal_confidence_bounds(self) -> None:
        with pytest.raises(Exception):
            ArbitrageSignal(
                strategy=ArbitrageStrategy.SPATIAL,
                buy_exchange="a",
                sell_exchange="b",
                symbol="X/Y",
                buy_price=100.0,
                sell_price=101.0,
                quantity=1.0,
                gross_spread_pct=1.0,
                net_spread_pct=0.5,
                estimated_profit_usd=0.5,
                confidence=1.5,  # exceeds max
                orderbook_depth_usd=1000.0,
            )

    def test_signal_with_metadata(self) -> None:
        signal = ArbitrageSignal(
            strategy=ArbitrageStrategy.TRIANGULAR,
            buy_exchange="binance",
            sell_exchange="binance",
            symbol="BTC/USDT",
            buy_price=50000.0,
            sell_price=50050.0,
            quantity=0.5,
            gross_spread_pct=0.1,
            net_spread_pct=0.05,
            estimated_profit_usd=12.5,
            confidence=0.6,
            orderbook_depth_usd=10000.0,
            metadata={"path": ["BTC/USDT", "ETH/BTC", "ETH/USDT"]},
        )
        assert signal.metadata is not None
        assert "path" in signal.metadata


# ---------------------------------------------------------------------------
# Balance tests
# ---------------------------------------------------------------------------


class TestExchangeBalance:
    """Tests for balance models."""

    def test_asset_balance_total(self) -> None:
        bal = AssetBalance(asset="BTC", free=1.5, locked=0.5, usd_value=100000.0)
        assert bal.total == pytest.approx(2.0)

    def test_exchange_balance_total_usd(self) -> None:
        eb = ExchangeBalance(
            exchange="binance",
            balances={
                "BTC": AssetBalance(asset="BTC", free=1.0, locked=0.0, usd_value=50000.0),
                "ETH": AssetBalance(asset="ETH", free=10.0, locked=0.0, usd_value=30000.0),
                "USDT": AssetBalance(asset="USDT", free=5000.0, locked=0.0, usd_value=5000.0),
            },
        )
        assert eb.total_usd_value == pytest.approx(85000.0)

    def test_exchange_balance_no_usd_values(self) -> None:
        eb = ExchangeBalance(
            exchange="test",
            balances={
                "BTC": AssetBalance(asset="BTC", free=1.0, locked=0.0),
            },
        )
        assert eb.total_usd_value == 0.0

    def test_portfolio_snapshot(self) -> None:
        snapshot = PortfolioSnapshot(
            exchange_balances={
                "binance": ExchangeBalance(
                    exchange="binance",
                    balances={
                        "BTC": AssetBalance(asset="BTC", free=1.0, usd_value=50000.0),
                    },
                ),
                "okx": ExchangeBalance(
                    exchange="okx",
                    balances={
                        "ETH": AssetBalance(asset="ETH", free=10.0, usd_value=30000.0),
                    },
                ),
            }
        )
        assert snapshot.total_usd_value == pytest.approx(80000.0)
        alloc = snapshot.allocation_by_exchange
        assert alloc["binance"] == pytest.approx(62.5)
        assert alloc["okx"] == pytest.approx(37.5)

    def test_portfolio_snapshot_empty(self) -> None:
        snapshot = PortfolioSnapshot()
        assert snapshot.total_usd_value == 0.0
        assert snapshot.allocation_by_exchange == {}


# ---------------------------------------------------------------------------
# Config model tests
# ---------------------------------------------------------------------------


class TestConfigModels:
    """Tests for configuration data models."""

    def test_trading_fee_frozen(self) -> None:
        fee = TradingFee(maker_pct=0.1, taker_pct=0.1)
        with pytest.raises(Exception):
            fee.maker_pct = 0.2  # type: ignore[misc]

    def test_risk_config_defaults(self) -> None:
        rc = RiskConfig()
        assert rc.max_position_per_coin_usd == 10_000
        assert rc.max_daily_loss_usd == 500
        assert rc.consecutive_loss_limit == 10
        assert rc.cooldown_minutes == 30
