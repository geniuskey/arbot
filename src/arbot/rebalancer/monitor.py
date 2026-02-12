"""Cross-exchange balance monitoring."""

from __future__ import annotations

from arbot.logging import get_logger
from arbot.models.balance import PortfolioSnapshot
from arbot.rebalancer.models import ImbalanceAlert

logger = get_logger("rebalancer.monitor")


class BalanceMonitor:
    """Monitor exchange balances and detect imbalances.

    Compares actual exchange allocations against target allocations
    and generates alerts when deviations exceed the threshold.

    Args:
        target_allocation: Mapping of exchange name to target percentage.
            If None, assumes equal split across all exchanges.
        imbalance_threshold_pct: Minimum deviation percentage to trigger an alert.
    """

    def __init__(
        self,
        target_allocation: dict[str, float] | None = None,
        imbalance_threshold_pct: float = 10.0,
    ) -> None:
        self._target_allocation = target_allocation
        self._imbalance_threshold_pct = imbalance_threshold_pct

    def check_imbalance(
        self, portfolio: PortfolioSnapshot
    ) -> list[ImbalanceAlert]:
        """Check for balance imbalances across exchanges.

        Args:
            portfolio: Current portfolio snapshot.

        Returns:
            List of imbalance alerts for exchanges deviating
            more than threshold from target allocation.
        """
        exchanges = list(portfolio.exchange_balances.keys())
        if len(exchanges) <= 1:
            return []

        total_usd = portfolio.total_usd_value
        if total_usd <= 0:
            return []

        current_alloc = self._compute_current_allocation(portfolio)
        target_alloc = self._get_target_allocation(exchanges)

        alerts: list[ImbalanceAlert] = []
        for exchange in exchanges:
            current_pct = current_alloc.get(exchange, 0.0)
            target_pct = target_alloc.get(exchange, 0.0)
            deviation = abs(current_pct - target_pct)

            if deviation >= self._imbalance_threshold_pct:
                action = self._determine_action(
                    exchange, current_pct, target_pct, total_usd
                )
                alerts.append(
                    ImbalanceAlert(
                        exchange=exchange,
                        asset="USD",
                        current_pct=round(current_pct, 2),
                        target_pct=round(target_pct, 2),
                        deviation_pct=round(deviation, 2),
                        suggested_action=action,
                    )
                )

        if alerts:
            logger.info(
                "imbalance_detected",
                num_alerts=len(alerts),
                exchanges=[a.exchange for a in alerts],
            )

        return alerts

    def _compute_current_allocation(
        self, portfolio: PortfolioSnapshot
    ) -> dict[str, float]:
        """Compute current allocation percentages by exchange.

        Args:
            portfolio: Current portfolio snapshot.

        Returns:
            Mapping of exchange to current percentage of total value.
        """
        return portfolio.allocation_by_exchange

    def _get_target_allocation(
        self, exchanges: list[str]
    ) -> dict[str, float]:
        """Get target allocation, defaulting to equal split.

        Args:
            exchanges: List of exchange names.

        Returns:
            Mapping of exchange to target percentage.
        """
        if self._target_allocation:
            return self._target_allocation
        equal_pct = 100.0 / len(exchanges)
        return {ex: equal_pct for ex in exchanges}

    def _determine_action(
        self,
        exchange: str,
        current_pct: float,
        target_pct: float,
        total_usd: float,
    ) -> str:
        """Determine a human-readable rebalance action.

        Args:
            exchange: Exchange name.
            current_pct: Current allocation percentage.
            target_pct: Target allocation percentage.
            total_usd: Total portfolio USD value.

        Returns:
            Human-readable action string.
        """
        diff_usd = abs(current_pct - target_pct) / 100.0 * total_usd
        diff_usd_rounded = round(diff_usd, 2)

        if current_pct > target_pct:
            return (
                f"Transfer ${diff_usd_rounded} worth of assets "
                f"FROM {exchange} to other exchanges"
            )
        else:
            return (
                f"Transfer ${diff_usd_rounded} worth of assets "
                f"TO {exchange} from other exchanges"
            )
