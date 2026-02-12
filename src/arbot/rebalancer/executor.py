"""Rebalancing executor in alert mode (Phase 2: alerts only, no auto-execution)."""

from __future__ import annotations

import time

from arbot.logging import get_logger
from arbot.models.balance import PortfolioSnapshot
from arbot.rebalancer.models import (
    ImbalanceAlert,
    RebalanceAlert,
    RebalancePlan,
    UrgencyLevel,
)
from arbot.rebalancer.monitor import BalanceMonitor
from arbot.rebalancer.optimizer import RebalancingOptimizer

logger = get_logger("rebalancer.executor")


class RebalancingExecutor:
    """Periodically check balances and generate rebalance alerts.

    Currently operates in alert-only mode. Generates alerts when
    rebalancing is needed but does not automatically execute transfers.

    Args:
        monitor: Balance monitor for detecting imbalances.
        optimizer: Optimizer for computing rebalance plans.
        min_alert_interval_seconds: Minimum seconds between alerts (throttling).
    """

    def __init__(
        self,
        monitor: BalanceMonitor,
        optimizer: RebalancingOptimizer,
        min_alert_interval_seconds: float = 3600.0,
    ) -> None:
        self._monitor = monitor
        self._optimizer = optimizer
        self._min_alert_interval_seconds = min_alert_interval_seconds
        self._last_alert_time: float | None = None

    async def run_check(
        self, portfolio: PortfolioSnapshot
    ) -> RebalanceAlert | None:
        """Run a balance check and return alert if rebalancing needed.

        Args:
            portfolio: Current portfolio snapshot.

        Returns:
            RebalanceAlert if rebalancing is needed and not throttled,
            None otherwise.
        """
        # Check throttling
        now = time.monotonic()
        if self._last_alert_time is not None:
            elapsed = now - self._last_alert_time
            if elapsed < self._min_alert_interval_seconds:
                logger.debug(
                    "alert_throttled",
                    elapsed=elapsed,
                    interval=self._min_alert_interval_seconds,
                )
                return None

        # Detect imbalances
        imbalances = self._monitor.check_imbalance(portfolio)
        if not imbalances:
            return None

        # Compute rebalance plan
        target_alloc = self._monitor._get_target_allocation(
            list(portfolio.exchange_balances.keys())
        )
        plan: RebalancePlan | None = None
        try:
            plan = self._optimizer.optimize(portfolio, target_alloc)
            if not plan.transfers:
                plan = None
        except Exception:
            logger.exception("rebalance_plan_failed")

        # Determine urgency and format message
        urgency = self._determine_urgency(imbalances)
        message = self._format_alert_message(imbalances, plan)

        self._last_alert_time = now

        logger.info(
            "rebalance_alert_generated",
            urgency=urgency.value,
            num_imbalances=len(imbalances),
        )

        return RebalanceAlert(
            imbalances=imbalances,
            suggested_plan=plan,
            urgency=urgency,
            message=message,
        )

    def _determine_urgency(
        self, imbalances: list[ImbalanceAlert]
    ) -> UrgencyLevel:
        """Determine urgency based on maximum deviation.

        Args:
            imbalances: List of detected imbalances.

        Returns:
            Urgency level based on deviation severity.
        """
        if not imbalances:
            return UrgencyLevel.LOW

        max_deviation = max(a.deviation_pct for a in imbalances)

        if max_deviation >= 40.0:
            return UrgencyLevel.CRITICAL
        elif max_deviation >= 25.0:
            return UrgencyLevel.HIGH
        elif max_deviation >= 15.0:
            return UrgencyLevel.MEDIUM
        else:
            return UrgencyLevel.LOW

    def _format_alert_message(
        self,
        imbalances: list[ImbalanceAlert],
        plan: RebalancePlan | None,
    ) -> str:
        """Format a human-readable alert message.

        Args:
            imbalances: List of detected imbalances.
            plan: Suggested rebalance plan, if available.

        Returns:
            Formatted alert message string.
        """
        lines: list[str] = ["[Rebalance Alert]"]
        lines.append("")

        for alert in imbalances:
            lines.append(
                f"  {alert.exchange}: {alert.current_pct:.1f}% "
                f"(target: {alert.target_pct:.1f}%, "
                f"deviation: {alert.deviation_pct:.1f}%)"
            )
            lines.append(f"    -> {alert.suggested_action}")

        if plan and plan.transfers:
            lines.append("")
            lines.append("Suggested transfers:")
            for t in plan.transfers:
                lines.append(
                    f"  {t.from_exchange} -> {t.to_exchange}: "
                    f"{t.amount} {t.asset} via {t.network} "
                    f"(fee: {t.estimated_fee})"
                )
            lines.append(
                f"  Total fee: ${plan.total_fee_estimate:.2f}, "
                f"ETA: {plan.estimated_duration_minutes:.0f} min"
            )

        return "\n".join(lines)
