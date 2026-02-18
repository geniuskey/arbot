"""Funding rate arbitrage position manager.

Manages the lifecycle of funding rate positions:
- Detect opportunities (via FundingRateDetector)
- Open hedged positions (spot long + simulated perp short)
- Collect simulated funding payments every 8h
- Close positions when no longer profitable
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from arbot.detector.funding import FundingRateDetector
from arbot.logging import get_logger
from arbot.models.funding import (
    FundingPosition,
    FundingPositionStatus,
    FundingRateSnapshot,
)

if TYPE_CHECKING:
    from arbot.connectors.base import BaseConnector
    from arbot.execution.paper_executor import PaperExecutor
    from arbot.risk.manager import RiskManager

logger = get_logger("funding.manager")

FUNDING_INTERVAL_HOURS = 8


@dataclass
class FundingStats:
    """Aggregate stats for funding rate arbitrage."""

    total_positions_opened: int = 0
    total_positions_closed: int = 0
    total_funding_collected: float = 0.0
    total_fees_paid: float = 0.0
    total_net_pnl: float = 0.0
    rate_checks: int = 0
    funding_settlements: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FundingRateManager:
    """Manages funding rate arbitrage positions in paper trading mode.

    Runs as an async loop alongside the spatial arb pipeline.
    Uses real funding rates but simulates position management.

    Args:
        detector: Funding rate detector for fetching/filtering rates.
        executor: Paper executor for balance management.
        risk_manager: Risk manager for trade recording.
        connectors: Exchange connectors for rate fetching.
        max_positions: Maximum concurrent funding positions.
        position_size_usd: USD size per position leg.
        close_threshold: Close if annualized rate drops below this %.
        check_interval_seconds: Seconds between rate checks.
    """

    def __init__(
        self,
        detector: FundingRateDetector,
        executor: PaperExecutor,
        risk_manager: RiskManager,
        connectors: list[BaseConnector],
        max_positions: int = 3,
        position_size_usd: float = 500.0,
        close_threshold: float = 5.0,
        check_interval_seconds: float = 300.0,
    ) -> None:
        self._detector = detector
        self._executor = executor
        self._risk_manager = risk_manager
        self._connectors = connectors
        self._max_positions = max_positions
        self._position_size_usd = position_size_usd
        self._close_threshold = close_threshold
        self._check_interval = check_interval_seconds

        self._positions: list[FundingPosition] = []
        self._closed_positions: list[FundingPosition] = []
        self._stats = FundingStats()
        self._latest_rates: dict[str, FundingRateSnapshot] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the funding rate management loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("funding_manager_started")

    async def stop(self) -> None:
        """Stop the funding rate management loop and clean up."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._detector.close()
        logger.info("funding_manager_stopped")

    async def _run_loop(self) -> None:
        """Main loop: fetch rates -> settle funding -> open/close positions."""
        while self._running:
            try:
                snapshots = await self._detector.fetch_rates(self._connectors)
                self._stats.rate_checks += 1

                for s in snapshots:
                    self._latest_rates[f"{s.exchange}:{s.symbol}"] = s

                self._settle_funding()
                self._evaluate_closes(snapshots)

                opportunities = self._detector.filter_opportunities(snapshots)
                if opportunities:
                    logger.info(
                        "funding_opportunities_found",
                        count=len(opportunities),
                        best_exchange=opportunities[0].exchange,
                        best_symbol=opportunities[0].symbol,
                        best_rate=opportunities[0].funding_rate,
                        best_annualized=round(opportunities[0].annualized_rate, 1),
                    )
                self._evaluate_opens(opportunities)

                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("funding_loop_error")
                await asyncio.sleep(self._check_interval)

    def _settle_funding(self) -> None:
        """Simulate funding payment collection for open positions.

        Checks if 8 hours have passed since last funding. If so,
        calculates payment and credits to executor balance.
        """
        now = datetime.now(UTC)

        for pos in self._positions:
            if pos.status != FundingPositionStatus.OPEN:
                continue

            last = pos.last_funding_at or pos.opened_at
            if last is None:
                continue

            hours_since = (now - last).total_seconds() / 3600
            if hours_since < FUNDING_INTERVAL_HOURS:
                continue

            periods = int(hours_since / FUNDING_INTERVAL_HOURS)

            key = f"{pos.exchange}:{pos.perp_symbol}"
            rate_snapshot = self._latest_rates.get(key)
            if rate_snapshot is None:
                continue

            notional = pos.quantity * rate_snapshot.mark_price
            payment_per_period = notional * rate_snapshot.funding_rate
            total_payment = payment_per_period * periods

            if total_payment > 0:
                self._executor._adjust_balance(pos.exchange, "USDT", total_payment)
                pos.total_funding_collected += total_payment
                pos.funding_payments += periods
                pos.last_funding_at = now
                self._stats.total_funding_collected += total_payment
                self._stats.funding_settlements += periods

                logger.info(
                    "funding_settled",
                    position_id=str(pos.id),
                    exchange=pos.exchange,
                    symbol=pos.symbol,
                    periods=periods,
                    payment=round(total_payment, 6),
                    rate=rate_snapshot.funding_rate,
                )

    def _evaluate_opens(self, opportunities: list[FundingRateSnapshot]) -> None:
        """Open new positions for the best opportunities."""
        open_count = sum(
            1 for p in self._positions if p.status == FundingPositionStatus.OPEN
        )

        for snapshot in opportunities:
            if open_count >= self._max_positions:
                break

            already_open = any(
                p.exchange == snapshot.exchange
                and p.perp_symbol == snapshot.symbol
                and p.status == FundingPositionStatus.OPEN
                for p in self._positions
            )
            if already_open:
                continue

            price = snapshot.index_price or snapshot.mark_price
            if price <= 0:
                continue

            spot_symbol = snapshot.symbol.split(":")[0]
            base_asset = spot_symbol.split("/")[0]
            quote_asset = spot_symbol.split("/")[1]

            quantity = self._position_size_usd / price
            quote_needed = self._position_size_usd

            balance = self._executor._get_balance(snapshot.exchange, quote_asset)
            if balance < quote_needed * 2:
                logger.info(
                    "funding_open_skip_balance",
                    exchange=snapshot.exchange,
                    symbol=spot_symbol,
                    balance=round(balance, 2),
                    needed=round(quote_needed * 2, 2),
                )
                continue

            # Spot buy: deduct USDT, add base asset
            spot_fee = quote_needed * 0.001  # ~0.1% taker
            self._executor._adjust_balance(
                snapshot.exchange, quote_asset, -(quote_needed + spot_fee)
            )
            self._executor._adjust_balance(snapshot.exchange, base_asset, quantity)

            # Perp margin: lock USDT as 1x collateral
            margin = quote_needed
            self._executor._adjust_balance(snapshot.exchange, quote_asset, -margin)

            total_entry_fees = spot_fee * 2  # spot + perp entry

            now = datetime.now(UTC)
            position = FundingPosition(
                exchange=snapshot.exchange,
                symbol=spot_symbol,
                perp_symbol=snapshot.symbol,
                status=FundingPositionStatus.OPEN,
                quantity=quantity,
                spot_entry_price=snapshot.index_price,
                perp_entry_price=snapshot.mark_price,
                total_fees=total_entry_fees,
                opened_at=now,
                last_funding_at=now,
            )

            self._positions.append(position)
            open_count += 1
            self._stats.total_positions_opened += 1
            self._stats.total_fees_paid += total_entry_fees

            logger.info(
                "funding_position_opened",
                position_id=str(position.id),
                exchange=snapshot.exchange,
                symbol=spot_symbol,
                quantity=round(quantity, 6),
                rate=snapshot.funding_rate,
                annualized=round(snapshot.annualized_rate, 1),
            )

    def _evaluate_closes(self, snapshots: list[FundingRateSnapshot]) -> None:
        """Close positions where funding rate dropped below threshold.

        Enforces a minimum holding period of 8h so that at least one
        funding payment is collected before incurring exit fees.
        Only a deeply negative rate bypasses this grace period.
        """
        rate_map: dict[str, FundingRateSnapshot] = {
            f"{s.exchange}:{s.symbol}": s for s in snapshots
        }

        for pos in list(self._positions):
            if pos.status != FundingPositionStatus.OPEN:
                continue

            key = f"{pos.exchange}:{pos.perp_symbol}"
            snapshot = rate_map.get(key)

            should_close = False
            reason = ""

            # Grace period: must hold at least 8h to collect one funding payment.
            # Only deeply negative rates (longs pay shorts heavily) bypass this.
            in_grace = pos.holding_hours < FUNDING_INTERVAL_HOURS

            if snapshot is None:
                if pos.holding_hours > 24:
                    should_close = True
                    reason = "rate_unavailable_24h"
            elif snapshot.funding_rate < -0.001:
                # Deeply negative: shorts pay longs, close immediately
                should_close = True
                reason = f"deeply_negative_rate_{snapshot.funding_rate:.6f}"
            elif not in_grace:
                # Past grace period: apply normal close rules
                if snapshot.funding_rate <= 0:
                    should_close = True
                    reason = f"negative_rate_{snapshot.funding_rate:.6f}"
                elif snapshot.annualized_rate < self._close_threshold:
                    should_close = True
                    reason = f"below_threshold_{snapshot.annualized_rate:.1f}pct"

            if should_close:
                self._close_position(pos, reason, snapshot)

    def _close_position(
        self,
        pos: FundingPosition,
        reason: str,
        snapshot: FundingRateSnapshot | None = None,
    ) -> None:
        """Close a funding position: sell spot + close perp short."""
        spot_symbol = pos.symbol
        base_asset = spot_symbol.split("/")[0]
        quote_asset = spot_symbol.split("/")[1]

        current_price = snapshot.index_price if snapshot else pos.spot_entry_price

        # Sell spot: remove base, add quote
        sell_proceeds = pos.quantity * current_price
        spot_fee = sell_proceeds * 0.001
        self._executor._adjust_balance(pos.exchange, base_asset, -pos.quantity)
        self._executor._adjust_balance(
            pos.exchange, quote_asset, sell_proceeds - spot_fee
        )

        # Close perp: return margin + PnL
        margin = pos.quantity * pos.perp_entry_price
        perp_pnl = pos.quantity * (pos.perp_entry_price - current_price)
        perp_fee = abs(sell_proceeds) * 0.001
        self._executor._adjust_balance(
            pos.exchange, quote_asset, margin + perp_pnl - perp_fee
        )

        close_fees = spot_fee + perp_fee
        pos.total_fees += close_fees
        pos.status = FundingPositionStatus.CLOSED
        pos.closed_at = datetime.now(UTC)
        pos.close_reason = reason

        self._closed_positions.append(pos)
        self._positions.remove(pos)
        self._stats.total_positions_closed += 1
        self._stats.total_fees_paid += close_fees
        self._stats.total_net_pnl += pos.net_pnl

        self._risk_manager.record_trade(pos.net_pnl)

        logger.info(
            "funding_position_closed",
            position_id=str(pos.id),
            exchange=pos.exchange,
            symbol=pos.symbol,
            reason=reason,
            funding_collected=round(pos.total_funding_collected, 6),
            net_pnl=round(pos.net_pnl, 6),
            holding_hours=round(pos.holding_hours, 1),
        )

    @property
    def open_positions(self) -> list[FundingPosition]:
        """Return all currently open positions."""
        return [p for p in self._positions if p.status == FundingPositionStatus.OPEN]

    def get_stats(self) -> FundingStats:
        """Return aggregate funding arbitrage statistics."""
        return self._stats

    @property
    def latest_rates(self) -> dict[str, FundingRateSnapshot]:
        """Return latest cached funding rate snapshots."""
        return dict(self._latest_rates)

    @property
    def is_running(self) -> bool:
        """Whether the manager loop is running."""
        return self._running
