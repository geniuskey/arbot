"""Funding rate arbitrage detector.

Fetches real funding rates from exchanges via ccxt REST and identifies
opportunities where the funding rate exceeds a threshold for delta-neutral
carry trade (spot long + perp short).

Creates dedicated futures/swap ccxt instances for exchanges where the
spot instance cannot access funding rate data (e.g. Binance, KuCoin).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import ccxt.async_support as ccxt

from arbot.logging import get_logger
from arbot.models.funding import FundingRateSnapshot

if TYPE_CHECKING:
    from arbot.connectors.base import BaseConnector

logger = get_logger("detector.funding")

# Exchanges that need a separate futures/swap ccxt instance.
# OKX and Bybit have unified APIs (spot instance works for futures).
_FUTURES_EXCHANGE_FACTORIES: dict[str, type] = {
    "binance": ccxt.binance,
    "kucoin": ccxt.kucoinfutures,
}

_FUTURES_OPTIONS: dict[str, dict[str, Any]] = {
    "binance": {"options": {"defaultType": "swap"}},
    "kucoin": {},
}


class FundingRateDetector:
    """Fetches and evaluates funding rates across exchanges.

    For exchanges with unified APIs (OKX, Bybit), uses the connector's
    existing ccxt instance. For others (Binance, KuCoin), creates
    dedicated futures ccxt instances (no API keys needed for public data).

    Args:
        min_rate_threshold: Minimum funding rate per 8h to consider.
        min_annualized_pct: Minimum annualized rate in percent.
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
        self._futures_instances: dict[str, Any] = {}

    async def _get_futures_exchange(self, exchange_name: str) -> Any | None:
        """Get or create a futures ccxt instance for an exchange.

        Returns None if the exchange doesn't need a separate instance
        (unified API exchanges like OKX, Bybit are handled via connector).
        """
        if exchange_name not in _FUTURES_EXCHANGE_FACTORIES:
            return None

        if exchange_name not in self._futures_instances:
            factory = _FUTURES_EXCHANGE_FACTORIES[exchange_name]
            options = _FUTURES_OPTIONS.get(exchange_name, {})
            instance = factory(options)
            self._futures_instances[exchange_name] = instance
            logger.info(
                "futures_ccxt_created",
                exchange=exchange_name,
            )

        return self._futures_instances[exchange_name]

    async def close(self) -> None:
        """Close all futures ccxt instances."""
        for name, instance in self._futures_instances.items():
            try:
                await instance.close()
            except Exception:
                logger.debug("futures_ccxt_close_error", exchange=name)
        self._futures_instances.clear()

    async def fetch_rates(
        self,
        connectors: list[BaseConnector],
    ) -> list[FundingRateSnapshot]:
        """Fetch current funding rates from all connectors via ccxt REST.

        For each exchange, tries a dedicated futures instance first.
        Falls back to the connector's spot instance (works for unified APIs).

        Args:
            connectors: List of connected exchange connectors.

        Returns:
            List of FundingRateSnapshot for all successful fetches.
        """
        snapshots: list[FundingRateSnapshot] = []

        for connector in connectors:
            exchange_name = connector.exchange_name

            # Try dedicated futures instance first, fall back to spot
            futures_ex = await self._get_futures_exchange(exchange_name)
            spot_ex: Any = getattr(connector, "_exchange", None)

            # Prefer futures instance, then spot (for unified APIs)
            exchanges_to_try = []
            if futures_ex is not None:
                exchanges_to_try.append(futures_ex)
            if spot_ex is not None:
                exchanges_to_try.append(spot_ex)

            if not exchanges_to_try:
                continue

            for symbol in self.symbols:
                fetched = False
                for ex in exchanges_to_try:
                    try:
                        data = await ex.fetch_funding_rate(symbol)
                        funding_ts = (
                            data.get("fundingTimestamp")
                            or data.get("timestamp")
                            or 0
                        )
                        mark = float(data.get("markPrice", 0) or 0)
                        index = float(data.get("indexPrice", 0) or 0)
                        # Fallback: use whichever is available
                        if mark <= 0 and index > 0:
                            mark = index
                        elif index <= 0 and mark > 0:
                            index = mark
                        snapshot = FundingRateSnapshot(
                            exchange=exchange_name,
                            symbol=symbol,
                            funding_rate=float(data.get("fundingRate", 0) or 0),
                            next_funding_time=datetime.fromtimestamp(
                                funding_ts / 1000 if funding_ts > 1e10 else funding_ts,
                                tz=UTC,
                            ),
                            mark_price=mark,
                            index_price=index,
                        )
                        snapshots.append(snapshot)
                        fetched = True
                        break
                    except Exception:
                        continue

                if not fetched:
                    logger.debug(
                        "funding_rate_fetch_failed",
                        exchange=exchange_name,
                        symbol=symbol,
                    )

        # Fill missing prices from other exchanges' snapshots
        symbol_prices: dict[str, float] = {}
        for s in snapshots:
            if s.index_price > 0 and s.symbol not in symbol_prices:
                symbol_prices[s.symbol] = s.index_price
        for s in snapshots:
            if s.index_price <= 0 and s.symbol in symbol_prices:
                s.index_price = symbol_prices[s.symbol]
            if s.mark_price <= 0 and s.index_price > 0:
                s.mark_price = s.index_price

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
        ]
        opportunities.sort(key=lambda s: s.funding_rate, reverse=True)
        return opportunities
