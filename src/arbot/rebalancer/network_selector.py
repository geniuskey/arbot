"""Optimal transfer network selection."""

from __future__ import annotations

from typing import Any

from arbot.logging import get_logger
from arbot.rebalancer.models import NetworkInfo

logger = get_logger("rebalancer.network_selector")

# Static data for common networks: asset -> network -> {fee, minutes, reliability}
NETWORK_DATA: dict[str, dict[str, dict[str, Any]]] = {
    "USDT": {
        "TRC20": {"fee": 1.0, "minutes": 3, "reliability": 0.99},
        "ERC20": {"fee": 15.0, "minutes": 5, "reliability": 0.99},
        "BEP20": {"fee": 0.5, "minutes": 3, "reliability": 0.97},
        "SOL": {"fee": 0.1, "minutes": 1, "reliability": 0.95},
        "POLYGON": {"fee": 0.1, "minutes": 5, "reliability": 0.95},
    },
    "BTC": {
        "BTC": {"fee": 0.0005, "minutes": 30, "reliability": 0.99},
        "BEP20": {"fee": 0.000005, "minutes": 3, "reliability": 0.97},
        "LIGHTNING": {"fee": 0.00001, "minutes": 1, "reliability": 0.90},
    },
    "ETH": {
        "ERC20": {"fee": 0.005, "minutes": 5, "reliability": 0.99},
        "BEP20": {"fee": 0.00005, "minutes": 3, "reliability": 0.97},
        "ARBITRUM": {"fee": 0.0001, "minutes": 2, "reliability": 0.96},
    },
}


class NetworkSelector:
    """Select optimal transfer network based on fee, speed, and reliability.

    Args:
        network_data: Custom network data. Uses built-in NETWORK_DATA if None.
    """

    def __init__(self, network_data: dict[str, dict[str, dict[str, Any]]] | None = None) -> None:
        self._network_data = network_data if network_data is not None else NETWORK_DATA

    def select_best(
        self,
        asset: str,
        amount: float,
        from_exchange: str | None = None,
        to_exchange: str | None = None,
    ) -> NetworkInfo | None:
        """Select the best network for transferring an asset.

        Args:
            asset: Asset symbol (e.g. "USDT").
            amount: Transfer amount in asset units.
            from_exchange: Source exchange (reserved for future filtering).
            to_exchange: Destination exchange (reserved for future filtering).

        Returns:
            Best NetworkInfo, or None if no networks available for the asset.
        """
        networks = self.get_available_networks(asset, amount)
        if not networks:
            return None
        return networks[0]

    def get_available_networks(
        self, asset: str, amount: float = 1000.0
    ) -> list[NetworkInfo]:
        """List all available networks for an asset, sorted by score (best first).

        Args:
            asset: Asset symbol.
            amount: Transfer amount for fee ratio calculation.

        Returns:
            List of NetworkInfo sorted by score descending.
        """
        asset_networks = self._network_data.get(asset)
        if not asset_networks:
            return []

        results: list[NetworkInfo] = []
        for network_name, data in asset_networks.items():
            fee = data["fee"]
            minutes = data["minutes"]
            reliability = data["reliability"]
            score = self._score_network(fee, minutes, reliability, amount)
            results.append(
                NetworkInfo(
                    network=network_name,
                    fee=fee,
                    estimated_minutes=minutes,
                    score=round(score, 4),
                )
            )

        results.sort(key=lambda n: n.score, reverse=True)
        return results

    def _score_network(
        self,
        fee: float,
        minutes: float,
        reliability: float,
        amount: float,
    ) -> float:
        """Score a network for transfer suitability.

        Higher score is better. Considers:
        - Fee as percentage of transfer amount (lower is better)
        - Transfer time (lower is better)
        - Reliability (higher is better)

        Args:
            fee: Transfer fee in asset units.
            minutes: Estimated transfer time.
            reliability: Network reliability (0-1).
            amount: Transfer amount for fee ratio calculation.

        Returns:
            Composite score (higher is better).
        """
        if amount <= 0:
            return 0.0

        fee_pct = (fee / amount) * 100.0
        # Score components: low fee%, low time, high reliability
        # fee_pct penalty: subtract fee_pct (so 1% fee = -1 point)
        # time penalty: subtract minutes / 10 (so 30min = -3 points)
        # reliability bonus: reliability * 10 (so 0.99 = +9.9 points)
        score = reliability * 10.0 - fee_pct - (minutes / 10.0)
        return max(score, 0.0)
