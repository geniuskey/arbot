"""Tests for arbot.risk.manager.RiskManager."""

from datetime import datetime, timedelta

from arbot.models.balance import AssetBalance, ExchangeBalance, PortfolioSnapshot
from arbot.models.config import RiskConfig
from arbot.models.signal import (
    ArbitrageSignal,
    ArbitrageStrategy,
    SignalStatus,
)
from arbot.risk.manager import RiskManager


def _make_signal(
    quantity: float = 0.01,
    buy_price: float = 50000.0,
    gross_spread_pct: float = 0.5,
    net_spread_pct: float = 0.3,
    estimated_profit_usd: float = 3.0,
) -> ArbitrageSignal:
    """Create a test arbitrage signal."""
    return ArbitrageSignal(
        strategy=ArbitrageStrategy.SPATIAL,
        buy_exchange="binance",
        sell_exchange="upbit",
        symbol="BTC/USDT",
        buy_price=buy_price,
        sell_price=buy_price * (1 + gross_spread_pct / 100),
        quantity=quantity,
        gross_spread_pct=gross_spread_pct,
        net_spread_pct=net_spread_pct,
        estimated_profit_usd=estimated_profit_usd,
        confidence=0.8,
        orderbook_depth_usd=5000.0,
        status=SignalStatus.DETECTED,
    )


def _make_portfolio(total_usd: float = 10000.0) -> PortfolioSnapshot:
    """Create a test portfolio snapshot."""
    return PortfolioSnapshot(
        exchange_balances={
            "binance": ExchangeBalance(
                exchange="binance",
                balances={
                    "USDT": AssetBalance(asset="USDT", free=total_usd / 2, usd_value=total_usd / 2),
                    "BTC": AssetBalance(asset="BTC", free=0.1, usd_value=total_usd / 2),
                },
            ),
        },
    )


class TestRiskManagerBasic:
    """Basic risk check tests."""

    def test_default_config_approves_normal_signal(self) -> None:
        rm = RiskManager()
        signal = _make_signal()
        portfolio = _make_portfolio()
        approved, reason = rm.check_signal(signal, portfolio)
        assert approved is True
        assert reason == "approved"

    def test_check_signal_returns_tuple(self) -> None:
        rm = RiskManager()
        signal = _make_signal()
        portfolio = _make_portfolio()
        result = rm.check_signal(signal, portfolio)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_position_size_exceeds_limit(self) -> None:
        config = RiskConfig(max_position_per_coin_usd=100.0)
        rm = RiskManager(config=config)
        # quantity * buy_price = 0.01 * 50000 = 500 > 100
        signal = _make_signal(quantity=0.01, buy_price=50000.0)
        portfolio = _make_portfolio()
        approved, reason = rm.check_signal(signal, portfolio)
        assert approved is False
        assert "position size" in reason

    def test_position_size_within_limit(self) -> None:
        config = RiskConfig(max_position_per_coin_usd=1000.0)
        rm = RiskManager(config=config)
        signal = _make_signal(quantity=0.01, buy_price=50000.0)
        portfolio = _make_portfolio()
        approved, _ = rm.check_signal(signal, portfolio)
        assert approved is True

    def test_anomalous_spread_rejected(self) -> None:
        config = RiskConfig(max_spread_pct=2.0)
        rm = RiskManager(config=config)
        signal = _make_signal(gross_spread_pct=5.0)
        portfolio = _make_portfolio()
        approved, reason = rm.check_signal(signal, portfolio)
        assert approved is False
        assert "spread" in reason

    def test_normal_spread_approved(self) -> None:
        config = RiskConfig(max_spread_pct=5.0)
        rm = RiskManager(config=config)
        signal = _make_signal(gross_spread_pct=3.0)
        portfolio = _make_portfolio()
        approved, _ = rm.check_signal(signal, portfolio)
        assert approved is True

    def test_price_deviation_rejected(self) -> None:
        config = RiskConfig(price_deviation_threshold_pct=5.0)
        rm = RiskManager(config=config)
        signal = _make_signal(net_spread_pct=8.0)
        portfolio = _make_portfolio()
        approved, reason = rm.check_signal(signal, portfolio)
        assert approved is False
        assert "net spread" in reason

    def test_total_exposure_exceeded(self) -> None:
        config = RiskConfig(max_total_exposure_usd=10000.0)
        rm = RiskManager(config=config)
        signal = _make_signal(quantity=0.01, buy_price=50000.0)
        portfolio = _make_portfolio(total_usd=10000.0)
        approved, reason = rm.check_signal(signal, portfolio)
        assert approved is False
        assert "exposure" in reason

    def test_total_exposure_within_limit(self) -> None:
        config = RiskConfig(max_total_exposure_usd=100000.0)
        rm = RiskManager(config=config)
        signal = _make_signal(quantity=0.01, buy_price=50000.0)
        portfolio = _make_portfolio(total_usd=10000.0)
        approved, _ = rm.check_signal(signal, portfolio)
        assert approved is True


class TestRiskManagerDailyLoss:
    """Daily loss tracking tests."""

    def test_daily_loss_limit_blocks_signals(self) -> None:
        config = RiskConfig(max_daily_loss_usd=100.0)
        rm = RiskManager(config=config)

        # Accumulate losses beyond limit
        rm.record_trade(-60.0)
        rm.record_trade(-50.0)  # total = -110

        signal = _make_signal()
        portfolio = _make_portfolio()
        approved, reason = rm.check_signal(signal, portfolio)
        assert approved is False
        assert "daily loss" in reason

    def test_daily_loss_under_limit_allows_signals(self) -> None:
        config = RiskConfig(max_daily_loss_usd=100.0)
        rm = RiskManager(config=config)

        rm.record_trade(-30.0)
        rm.record_trade(-20.0)  # total = -50, under 100

        signal = _make_signal()
        portfolio = _make_portfolio()
        approved, _ = rm.check_signal(signal, portfolio)
        assert approved is True

    def test_daily_pnl_property(self) -> None:
        rm = RiskManager()
        rm.record_trade(50.0)
        rm.record_trade(-20.0)
        assert rm.daily_pnl == 30.0

    def test_reset_daily(self) -> None:
        rm = RiskManager()
        rm.record_trade(-100.0)
        assert rm.daily_pnl == -100.0

        rm.reset_daily()
        assert rm.daily_pnl == 0.0


class TestRiskManagerCircuitBreaker:
    """Circuit breaker and cooldown tests."""

    def test_consecutive_losses_trigger_cooldown(self) -> None:
        config = RiskConfig(consecutive_loss_limit=3, cooldown_minutes=5)
        rm = RiskManager(config=config)

        rm.record_trade(-10.0)
        rm.record_trade(-10.0)
        assert rm.is_in_cooldown is False

        rm.record_trade(-10.0)  # 3rd consecutive loss
        assert rm.is_in_cooldown is True

        signal = _make_signal()
        portfolio = _make_portfolio()
        approved, reason = rm.check_signal(signal, portfolio)
        assert approved is False
        assert "cooldown" in reason

    def test_winning_trade_resets_consecutive_losses(self) -> None:
        config = RiskConfig(consecutive_loss_limit=3)
        rm = RiskManager(config=config)

        rm.record_trade(-10.0)
        rm.record_trade(-10.0)
        rm.record_trade(5.0)  # Resets counter
        assert rm.consecutive_losses == 0

        rm.record_trade(-10.0)
        assert rm.consecutive_losses == 1

    def test_cooldown_expires(self) -> None:
        config = RiskConfig(consecutive_loss_limit=2, cooldown_minutes=5)
        rm = RiskManager(config=config)

        rm.record_trade(-10.0)
        rm.record_trade(-10.0)
        assert rm.is_in_cooldown is True

        # Manually set cooldown to the past
        rm._cooldown_until = datetime.utcnow() - timedelta(minutes=1)
        assert rm.is_in_cooldown is False

        signal = _make_signal()
        portfolio = _make_portfolio()
        approved, _ = rm.check_signal(signal, portfolio)
        assert approved is True

    def test_trade_count(self) -> None:
        rm = RiskManager()
        assert rm.trade_count == 0
        rm.record_trade(10.0)
        rm.record_trade(-5.0)
        assert rm.trade_count == 2
