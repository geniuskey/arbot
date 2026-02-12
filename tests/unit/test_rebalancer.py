"""Tests for arbot.rebalancer module."""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import patch

import pytest

from arbot.models.balance import AssetBalance, ExchangeBalance, PortfolioSnapshot
from arbot.rebalancer.executor import RebalancingExecutor
from arbot.rebalancer.models import (
    ImbalanceAlert,
    NetworkInfo,
    RebalanceAlert,
    RebalancePlan,
    Transfer,
    UrgencyLevel,
)
from arbot.rebalancer.monitor import BalanceMonitor
from arbot.rebalancer.network_selector import NETWORK_DATA, NetworkSelector
from arbot.rebalancer.optimizer import RebalancingOptimizer


# --- Helpers ---


def _make_portfolio(
    allocations: dict[str, float],
) -> PortfolioSnapshot:
    """Create a portfolio with given USD allocations per exchange.

    Args:
        allocations: Mapping of exchange name to USD value.
    """
    exchange_balances: dict[str, ExchangeBalance] = {}
    for exchange, usd_value in allocations.items():
        exchange_balances[exchange] = ExchangeBalance(
            exchange=exchange,
            balances={
                "USDT": AssetBalance(
                    asset="USDT", free=usd_value, usd_value=usd_value
                ),
            },
        )
    return PortfolioSnapshot(exchange_balances=exchange_balances)


# =============================================================================
# BalanceMonitor Tests
# =============================================================================


class TestBalanceMonitor:
    """Tests for BalanceMonitor."""

    def test_balanced_50_50_no_alerts(self) -> None:
        """Equal allocation with 50/50 split should generate no alerts."""
        monitor = BalanceMonitor(imbalance_threshold_pct=10.0)
        portfolio = _make_portfolio({"binance": 5000, "upbit": 5000})

        alerts = monitor.check_imbalance(portfolio)

        assert alerts == []

    def test_imbalanced_70_30_generates_alerts(self) -> None:
        """70/30 split with 10% threshold should alert on both exchanges."""
        monitor = BalanceMonitor(imbalance_threshold_pct=10.0)
        portfolio = _make_portfolio({"binance": 7000, "upbit": 3000})

        alerts = monitor.check_imbalance(portfolio)

        assert len(alerts) == 2
        exchange_names = {a.exchange for a in alerts}
        assert exchange_names == {"binance", "upbit"}

        # Check deviations: target is 50% each
        for alert in alerts:
            assert alert.deviation_pct == 20.0
            if alert.exchange == "binance":
                assert alert.current_pct == 70.0
                assert alert.target_pct == 50.0
            else:
                assert alert.current_pct == 30.0
                assert alert.target_pct == 50.0

    def test_custom_target_allocation(self) -> None:
        """Custom target allocation should be used for comparison."""
        # Target: binance 70%, upbit 30%
        monitor = BalanceMonitor(
            target_allocation={"binance": 70.0, "upbit": 30.0},
            imbalance_threshold_pct=10.0,
        )
        # Actual: 70/30 matches target
        portfolio = _make_portfolio({"binance": 7000, "upbit": 3000})

        alerts = monitor.check_imbalance(portfolio)

        assert alerts == []

    def test_custom_target_with_deviation(self) -> None:
        """Custom target that doesn't match actual should alert."""
        # Target: binance 80%, upbit 20%
        monitor = BalanceMonitor(
            target_allocation={"binance": 80.0, "upbit": 20.0},
            imbalance_threshold_pct=10.0,
        )
        # Actual: 50/50
        portfolio = _make_portfolio({"binance": 5000, "upbit": 5000})

        alerts = monitor.check_imbalance(portfolio)

        assert len(alerts) == 2

    def test_single_exchange_no_imbalance(self) -> None:
        """Single exchange should never generate imbalance alerts."""
        monitor = BalanceMonitor(imbalance_threshold_pct=1.0)
        portfolio = _make_portfolio({"binance": 10000})

        alerts = monitor.check_imbalance(portfolio)

        assert alerts == []

    def test_zero_total_value_no_alerts(self) -> None:
        """Zero portfolio value should not produce alerts."""
        monitor = BalanceMonitor(imbalance_threshold_pct=1.0)
        portfolio = _make_portfolio({"binance": 0, "upbit": 0})

        alerts = monitor.check_imbalance(portfolio)

        assert alerts == []

    def test_three_exchanges_equal_split(self) -> None:
        """Three exchanges with equal balance should have no alerts."""
        monitor = BalanceMonitor(imbalance_threshold_pct=10.0)
        portfolio = _make_portfolio(
            {"binance": 3333, "upbit": 3333, "okx": 3334}
        )

        alerts = monitor.check_imbalance(portfolio)

        assert alerts == []

    def test_below_threshold_no_alert(self) -> None:
        """Deviation just below threshold should not generate alert."""
        monitor = BalanceMonitor(imbalance_threshold_pct=10.0)
        # 55/45 split: deviation = 5% from 50% target
        portfolio = _make_portfolio({"binance": 5500, "upbit": 4500})

        alerts = monitor.check_imbalance(portfolio)

        assert alerts == []

    def test_suggested_action_surplus(self) -> None:
        """Surplus exchange should suggest transferring FROM."""
        monitor = BalanceMonitor(imbalance_threshold_pct=10.0)
        portfolio = _make_portfolio({"binance": 8000, "upbit": 2000})

        alerts = monitor.check_imbalance(portfolio)

        binance_alert = next(a for a in alerts if a.exchange == "binance")
        assert "FROM binance" in binance_alert.suggested_action

    def test_suggested_action_deficit(self) -> None:
        """Deficit exchange should suggest transferring TO."""
        monitor = BalanceMonitor(imbalance_threshold_pct=10.0)
        portfolio = _make_portfolio({"binance": 8000, "upbit": 2000})

        alerts = monitor.check_imbalance(portfolio)

        upbit_alert = next(a for a in alerts if a.exchange == "upbit")
        assert "TO upbit" in upbit_alert.suggested_action


# =============================================================================
# NetworkSelector Tests
# =============================================================================


class TestNetworkSelector:
    """Tests for NetworkSelector."""

    def test_usdt_best_network(self) -> None:
        """USDT best network should be selected based on score."""
        selector = NetworkSelector()
        best = selector.select_best("USDT", amount=1000.0)

        assert best is not None
        assert best.network in {"TRC20", "BEP20", "SOL", "POLYGON"}
        # For 1000 USDT, low-fee networks with good reliability should win

    def test_unknown_asset_returns_none(self) -> None:
        """Unknown asset should return None."""
        selector = NetworkSelector()
        result = selector.select_best("UNKNOWN_COIN", amount=1000.0)

        assert result is None

    def test_get_available_networks_usdt(self) -> None:
        """USDT should have all known networks listed."""
        selector = NetworkSelector()
        networks = selector.get_available_networks("USDT")

        assert len(networks) == 5
        network_names = {n.network for n in networks}
        assert network_names == {"TRC20", "ERC20", "BEP20", "SOL", "POLYGON"}

    def test_get_available_networks_unknown(self) -> None:
        """Unknown asset should return empty list."""
        selector = NetworkSelector()
        networks = selector.get_available_networks("UNKNOWN")

        assert networks == []

    def test_networks_sorted_by_score_descending(self) -> None:
        """Networks should be sorted by score, best first."""
        selector = NetworkSelector()
        networks = selector.get_available_networks("USDT", amount=1000.0)

        scores = [n.score for n in networks]
        assert scores == sorted(scores, reverse=True)

    def test_score_respects_fee_to_amount_ratio(self) -> None:
        """High fee relative to small amount should lower score."""
        selector = NetworkSelector()

        # For small amounts, high-fee networks are penalized more
        small_networks = selector.get_available_networks("USDT", amount=10.0)
        large_networks = selector.get_available_networks("USDT", amount=100000.0)

        # ERC20 (fee=15) should rank worse for small amounts
        small_erc20 = next(n for n in small_networks if n.network == "ERC20")
        large_erc20 = next(n for n in large_networks if n.network == "ERC20")
        assert small_erc20.score < large_erc20.score

    def test_btc_networks(self) -> None:
        """BTC should have known networks."""
        selector = NetworkSelector()
        best = selector.select_best("BTC", amount=0.1)

        assert best is not None
        assert best.network in {"BTC", "BEP20", "LIGHTNING"}

    def test_custom_network_data(self) -> None:
        """Custom network data should override defaults."""
        custom_data = {
            "DOGE": {
                "DOGE": {"fee": 2.0, "minutes": 10, "reliability": 0.98},
            },
        }
        selector = NetworkSelector(network_data=custom_data)

        result = selector.select_best("DOGE", amount=1000.0)
        assert result is not None
        assert result.network == "DOGE"

        # Default assets should not be available
        assert selector.select_best("USDT", amount=1000.0) is None

    def test_score_zero_amount(self) -> None:
        """Zero amount should return 0 score."""
        selector = NetworkSelector()
        score = selector._score_network(fee=1.0, minutes=3.0, reliability=0.99, amount=0.0)
        assert score == 0.0


# =============================================================================
# RebalancingOptimizer Tests
# =============================================================================


class TestRebalancingOptimizer:
    """Tests for RebalancingOptimizer."""

    def test_simple_two_exchange_rebalance(self) -> None:
        """Two exchanges with imbalance should produce one transfer."""
        optimizer = RebalancingOptimizer(min_transfer_usd=50.0)
        portfolio = _make_portfolio({"binance": 7000, "upbit": 3000})
        target = {"binance": 50.0, "upbit": 50.0}

        plan = optimizer.optimize(portfolio, target)

        assert len(plan.transfers) == 1
        t = plan.transfers[0]
        assert t.from_exchange == "binance"
        assert t.to_exchange == "upbit"
        assert t.amount == 2000.0
        assert t.asset == "USDT"
        assert t.network  # Some network was selected
        assert plan.total_fee_estimate >= 0

    def test_no_transfers_when_balanced(self) -> None:
        """Balanced portfolio should produce no transfers."""
        optimizer = RebalancingOptimizer()
        portfolio = _make_portfolio({"binance": 5000, "upbit": 5000})
        target = {"binance": 50.0, "upbit": 50.0}

        plan = optimizer.optimize(portfolio, target)

        assert plan.transfers == []
        assert plan.total_fee_estimate == 0.0

    def test_min_transfer_amount_filtering(self) -> None:
        """Transfers below minimum should be filtered out."""
        optimizer = RebalancingOptimizer(min_transfer_usd=500.0)
        # 52/48 split = $200 deviation, below $500 min
        portfolio = _make_portfolio({"binance": 5200, "upbit": 4800})
        target = {"binance": 50.0, "upbit": 50.0}

        plan = optimizer.optimize(portfolio, target)

        assert plan.transfers == []

    def test_three_exchange_rebalance(self) -> None:
        """Three exchanges should produce valid transfers."""
        optimizer = RebalancingOptimizer(min_transfer_usd=50.0)
        portfolio = _make_portfolio(
            {"binance": 6000, "upbit": 2000, "okx": 2000}
        )
        target = {"binance": 33.33, "upbit": 33.33, "okx": 33.34}

        plan = optimizer.optimize(portfolio, target)

        assert len(plan.transfers) >= 1
        # Total should flow from binance to upbit/okx
        for t in plan.transfers:
            assert t.from_exchange == "binance"
            assert t.to_exchange in {"upbit", "okx"}

    def test_zero_portfolio_value(self) -> None:
        """Zero portfolio should produce empty plan."""
        optimizer = RebalancingOptimizer()
        portfolio = _make_portfolio({"binance": 0, "upbit": 0})
        target = {"binance": 50.0, "upbit": 50.0}

        plan = optimizer.optimize(portfolio, target)

        assert plan.transfers == []
        assert plan.total_fee_estimate == 0.0

    def test_plan_has_network_info(self) -> None:
        """Transfers should have valid network information."""
        optimizer = RebalancingOptimizer(min_transfer_usd=50.0)
        portfolio = _make_portfolio({"binance": 8000, "upbit": 2000})
        target = {"binance": 50.0, "upbit": 50.0}

        plan = optimizer.optimize(portfolio, target)

        assert len(plan.transfers) == 1
        t = plan.transfers[0]
        assert t.network  # Network should be set
        assert t.estimated_fee >= 0
        assert plan.estimated_duration_minutes >= 0


# =============================================================================
# RebalancingExecutor Tests
# =============================================================================


class TestRebalancingExecutor:
    """Tests for RebalancingExecutor."""

    def _make_executor(
        self,
        threshold: float = 10.0,
        min_alert_interval: float = 3600.0,
    ) -> RebalancingExecutor:
        """Create a test executor with default components."""
        monitor = BalanceMonitor(imbalance_threshold_pct=threshold)
        optimizer = RebalancingOptimizer(min_transfer_usd=50.0)
        return RebalancingExecutor(
            monitor=monitor,
            optimizer=optimizer,
            min_alert_interval_seconds=min_alert_interval,
        )

    @pytest.mark.asyncio
    async def test_alert_generated_when_imbalanced(self) -> None:
        """Should generate alert when portfolio is imbalanced."""
        executor = self._make_executor(threshold=10.0)
        portfolio = _make_portfolio({"binance": 8000, "upbit": 2000})

        alert = await executor.run_check(portfolio)

        assert alert is not None
        assert len(alert.imbalances) == 2
        assert alert.urgency in (
            UrgencyLevel.LOW,
            UrgencyLevel.MEDIUM,
            UrgencyLevel.HIGH,
            UrgencyLevel.CRITICAL,
        )
        assert alert.message
        assert "[Rebalance Alert]" in alert.message

    @pytest.mark.asyncio
    async def test_no_alert_when_balanced(self) -> None:
        """Should not generate alert when balanced."""
        executor = self._make_executor(threshold=10.0)
        portfolio = _make_portfolio({"binance": 5000, "upbit": 5000})

        alert = await executor.run_check(portfolio)

        assert alert is None

    @pytest.mark.asyncio
    async def test_throttling_suppresses_second_alert(self) -> None:
        """Second alert within interval should be suppressed."""
        executor = self._make_executor(
            threshold=10.0, min_alert_interval=3600.0
        )
        portfolio = _make_portfolio({"binance": 8000, "upbit": 2000})

        # First alert should succeed
        alert1 = await executor.run_check(portfolio)
        assert alert1 is not None

        # Second alert should be throttled
        alert2 = await executor.run_check(portfolio)
        assert alert2 is None

    @pytest.mark.asyncio
    async def test_no_throttling_after_interval(self) -> None:
        """Alert should be allowed after interval passes."""
        executor = self._make_executor(
            threshold=10.0, min_alert_interval=0.01
        )
        portfolio = _make_portfolio({"binance": 8000, "upbit": 2000})

        alert1 = await executor.run_check(portfolio)
        assert alert1 is not None

        # Wait for interval to pass
        import asyncio
        await asyncio.sleep(0.02)

        alert2 = await executor.run_check(portfolio)
        assert alert2 is not None

    @pytest.mark.asyncio
    async def test_urgency_low_for_small_deviation(self) -> None:
        """Small deviation should produce LOW urgency."""
        executor = self._make_executor(threshold=5.0)
        # 58/42 split: 8% deviation
        portfolio = _make_portfolio({"binance": 5800, "upbit": 4200})

        alert = await executor.run_check(portfolio)

        assert alert is not None
        assert alert.urgency == UrgencyLevel.LOW

    @pytest.mark.asyncio
    async def test_urgency_medium_for_moderate_deviation(self) -> None:
        """Moderate deviation should produce MEDIUM urgency."""
        executor = self._make_executor(threshold=5.0)
        # 70/30 split: 20% deviation
        portfolio = _make_portfolio({"binance": 7000, "upbit": 3000})

        alert = await executor.run_check(portfolio)

        assert alert is not None
        assert alert.urgency == UrgencyLevel.MEDIUM

    @pytest.mark.asyncio
    async def test_urgency_high_for_large_deviation(self) -> None:
        """Large deviation should produce HIGH urgency."""
        executor = self._make_executor(threshold=5.0)
        # 88/12 split: 38% deviation
        portfolio = _make_portfolio({"binance": 8800, "upbit": 1200})

        alert = await executor.run_check(portfolio)

        assert alert is not None
        assert alert.urgency == UrgencyLevel.HIGH

    @pytest.mark.asyncio
    async def test_urgency_critical_for_extreme_deviation(self) -> None:
        """Extreme deviation should produce CRITICAL urgency."""
        executor = self._make_executor(threshold=5.0)
        # 96/4 split: 46% deviation
        portfolio = _make_portfolio({"binance": 9600, "upbit": 400})

        alert = await executor.run_check(portfolio)

        assert alert is not None
        assert alert.urgency == UrgencyLevel.CRITICAL

    @pytest.mark.asyncio
    async def test_alert_includes_suggested_plan(self) -> None:
        """Alert should include a suggested rebalance plan."""
        executor = self._make_executor(threshold=10.0)
        portfolio = _make_portfolio({"binance": 8000, "upbit": 2000})

        alert = await executor.run_check(portfolio)

        assert alert is not None
        assert alert.suggested_plan is not None
        assert len(alert.suggested_plan.transfers) >= 1

    @pytest.mark.asyncio
    async def test_alert_message_contains_exchange_info(self) -> None:
        """Alert message should contain exchange and deviation info."""
        executor = self._make_executor(threshold=10.0)
        portfolio = _make_portfolio({"binance": 8000, "upbit": 2000})

        alert = await executor.run_check(portfolio)

        assert alert is not None
        assert "binance" in alert.message
        assert "upbit" in alert.message


# =============================================================================
# Model Tests
# =============================================================================


class TestRebalancerModels:
    """Tests for rebalancer data models."""

    def test_imbalance_alert_frozen(self) -> None:
        """ImbalanceAlert should be immutable."""
        alert = ImbalanceAlert(
            exchange="binance",
            asset="USDT",
            current_pct=70.0,
            target_pct=50.0,
            deviation_pct=20.0,
            suggested_action="Transfer $2000 FROM binance",
        )
        with pytest.raises(Exception):
            alert.exchange = "upbit"  # type: ignore[misc]

    def test_transfer_frozen(self) -> None:
        """Transfer should be immutable."""
        transfer = Transfer(
            from_exchange="binance",
            to_exchange="upbit",
            asset="USDT",
            amount=1000.0,
            network="TRC20",
            estimated_fee=1.0,
        )
        with pytest.raises(Exception):
            transfer.amount = 2000.0  # type: ignore[misc]

    def test_rebalance_plan_frozen(self) -> None:
        """RebalancePlan should be immutable."""
        plan = RebalancePlan(
            transfers=[],
            total_fee_estimate=0.0,
            estimated_duration_minutes=0.0,
        )
        with pytest.raises(Exception):
            plan.total_fee_estimate = 999.0  # type: ignore[misc]

    def test_urgency_level_values(self) -> None:
        """UrgencyLevel should have expected values."""
        assert UrgencyLevel.LOW.value == "low"
        assert UrgencyLevel.MEDIUM.value == "medium"
        assert UrgencyLevel.HIGH.value == "high"
        assert UrgencyLevel.CRITICAL.value == "critical"
