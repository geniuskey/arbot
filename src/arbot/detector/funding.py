"""Funding rate arbitrage detector.

Fetches real funding rates from exchanges via ccxt REST and identifies
opportunities where the funding rate exceeds a threshold for delta-neutral
carry trade (spot long + perp short).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from arbot.logging import get_logger
from arbot.models.funding import FundingRateSnapshot

if TYPE_CHECKING:
    from arbot.connectors.base import BaseConnector

logger = get_logger("detector.funding")


class FundingRateDetector:
    """Fetches and evaluates funding rates across exchanges.

    Args:
        min_rate_threshold: Minimum funding rate per 8h to consider (e.g. 0.0001 = 0.01%).
        min_annualized_pct: Minimum annualized rate in percent (e.g. 10.0).
        symbols: Perpetual futures symbols to monitor (ccxt format).
    """

    def __init__(
        self,
        min_rate_threshold: float = 0.0001,
        min_annualized_pct: float = 10.0,
        symbols: list[str] | None = None,
    ) -> None:
        self.min_rate_threshold = min_rate_threshold
        self.min_annualized_pct = min_annualized_pct
        self.symbols = symbols or ["BTC/USDT:USDT", "ETH/USDT:USDT"]

    async def fetch_rates(
        self,
        connectors: list[BaseConnector],
    ) -> list[FundingRateSnapshot]:
        """Fetch current funding rates from all connectors via ccxt REST.

        Uses ccxt's fetch_funding_rate() for each symbol on each exchange.
        Failures are logged and skipped.

        Args:
            connectors: List of connected exchange connectors.

        Returns:
            List of FundingRateSnapshot for all successful fetches.
        """
        snapshots: list[FundingRateSnapshot] = []

        for connector in connectors:
            exchange: Any = getattr(connector, "_exchange", None)
            if exchange is None:
                continue

            for symbol in self.symbols:
                try:
                    data = await exchange.fetch_funding_rate(symbol)
                    funding_ts = data.get("fundingTimestamp") or data.get("timestamp") or 0
                    snapshot = FundingRateSnapshot(
                        exchange=connector.exchange_name,
                        symbol=symbol,
                        funding_rate=float(data.get("fundingRate", 0) or 0),
                        next_funding_time=datetime.fromtimestamp(
                            funding_ts / 1000 if funding_ts > 1e10 else funding_ts,
                            tz=UTC,
                        ),
                        mark_price=float(data.get("markPrice", 0) or 0),
                        index_price=float(data.get("indexPrice", 0) or 0),
                    )
                    snapshots.append(snapshot)
                except Exception as e:
                    logger.debug(
                        "funding_rate_fetch_failed",
                        exchange=connector.exchange_name,
                        symbol=symbol,
                        error=str(e),
                    )

        return snapshots

    def filter_opportunities(
        self,
        snapshots: list[FundingRateSnapshot],
    ) -> list[FundingRateSnapshot]:
        """Filter snapshots that meet profitability thresholds.

        Only positive funding rates are opportunities (longs pay shorts,
        so our short position collects the payment).

        Args:
            snapshots: Raw funding rate snapshots.

        Returns:
            Filtered list sorted by funding_rate descending.
        """
        opportunities = [
            s
            for s in snapshots
            if s.funding_rate >= self.min_rate_threshold
            and s.annualized_rate >= self.min_annualized_pct
            and s.mark_price > 0
        ]
        opportunities.sort(key=lambda s: s.funding_rate, reverse=True)
        return opportunities
