"""Optimal transfer plan computation."""

from __future__ import annotations

from arbot.logging import get_logger
from arbot.models.balance import PortfolioSnapshot
from arbot.rebalancer.models import RebalancePlan, Transfer
from arbot.rebalancer.network_selector import NetworkSelector

logger = get_logger("rebalancer.optimizer")


class RebalancingOptimizer:
    """Compute optimal transfers to achieve target allocation.

    Args:
        network_selector: Network selector for choosing transfer networks.
            Uses default NetworkSelector if None.
        min_transfer_usd: Minimum transfer amount in USD to include in plan.
    """

    def __init__(
        self,
        network_selector: NetworkSelector | None = None,
        min_transfer_usd: float = 100.0,
    ) -> None:
        self._network_selector = network_selector or NetworkSelector()
        self._min_transfer_usd = min_transfer_usd

    def optimize(
        self,
        portfolio: PortfolioSnapshot,
        target_allocation: dict[str, float],
    ) -> RebalancePlan:
        """Compute optimal rebalance plan.

        Algorithm:
        1. Calculate deviation from target for each exchange
        2. Match surplus exchanges with deficit exchanges
        3. For each transfer, select best network
        4. Filter transfers below minimum amount
        5. Return plan with total fee estimate

        Args:
            portfolio: Current portfolio snapshot.
            target_allocation: Target allocation percentages by exchange.

        Returns:
            RebalancePlan with computed transfers.
        """
        total_usd = portfolio.total_usd_value
        if total_usd <= 0:
            return RebalancePlan(
                transfers=[], total_fee_estimate=0.0, estimated_duration_minutes=0.0
            )

        # Step 1: Calculate deviations (positive = surplus, negative = deficit)
        deviations: dict[str, float] = {}
        for exchange, eb in portfolio.exchange_balances.items():
            current_pct = (eb.total_usd_value / total_usd) * 100.0
            target_pct = target_allocation.get(exchange, 0.0)
            deviation_usd = (current_pct - target_pct) / 100.0 * total_usd
            deviations[exchange] = deviation_usd

        # Step 2: Separate surplus and deficit exchanges
        surplus = {
            ex: amt for ex, amt in deviations.items() if amt > 0
        }
        deficit = {
            ex: -amt for ex, amt in deviations.items() if amt < 0
        }

        # Step 3: Match surplus with deficit (greedy)
        transfers: list[Transfer] = []
        surplus_list = sorted(surplus.items(), key=lambda x: x[1], reverse=True)
        deficit_list = sorted(deficit.items(), key=lambda x: x[1], reverse=True)

        si = 0
        di = 0
        surplus_remaining = {ex: amt for ex, amt in surplus_list}
        deficit_remaining = {ex: amt for ex, amt in deficit_list}

        while si < len(surplus_list) and di < len(deficit_list):
            src_exchange = surplus_list[si][0]
            dst_exchange = deficit_list[di][0]
            src_avail = surplus_remaining[src_exchange]
            dst_need = deficit_remaining[dst_exchange]

            transfer_amount = min(src_avail, dst_need)

            if transfer_amount >= self._min_transfer_usd:
                # Use USDT as default transfer asset
                asset = "USDT"
                network_info = self._network_selector.select_best(
                    asset=asset, amount=transfer_amount
                )

                if network_info:
                    transfers.append(
                        Transfer(
                            from_exchange=src_exchange,
                            to_exchange=dst_exchange,
                            asset=asset,
                            amount=round(transfer_amount, 2),
                            network=network_info.network,
                            estimated_fee=network_info.fee,
                        )
                    )

            surplus_remaining[src_exchange] -= transfer_amount
            deficit_remaining[dst_exchange] -= transfer_amount

            if surplus_remaining[src_exchange] < 1.0:
                si += 1
            if deficit_remaining[dst_exchange] < 1.0:
                di += 1

        # Step 5: Compute totals
        total_fee = sum(t.estimated_fee for t in transfers)
        max_duration = (
            max(
                self._network_selector.select_best(t.asset, t.amount).estimated_minutes
                for t in transfers
                if self._network_selector.select_best(t.asset, t.amount)
            )
            if transfers
            else 0.0
        )

        logger.info(
            "rebalance_plan_computed",
            num_transfers=len(transfers),
            total_fee=total_fee,
            estimated_minutes=max_duration,
        )

        return RebalancePlan(
            transfers=transfers,
            total_fee_estimate=round(total_fee, 4),
            estimated_duration_minutes=max_duration,
        )
