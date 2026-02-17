"""Funding rate arbitrage data models."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class FundingPositionStatus(str, enum.Enum):
    """Lifecycle status of a funding rate position."""

    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class FundingRateSnapshot(BaseModel):
    """Point-in-time funding rate data from an exchange.

    Attributes:
        exchange: Exchange name.
        symbol: Perpetual futures symbol (e.g. "BTC/USDT:USDT").
        funding_rate: Current funding rate (e.g. 0.0001 = 0.01%).
        next_funding_time: UTC timestamp of next funding settlement.
        mark_price: Current mark price.
        index_price: Current index (spot) price.
        fetched_at: When this snapshot was fetched.
    """

    exchange: str
    symbol: str
    funding_rate: float
    next_funding_time: datetime
    mark_price: float
    index_price: float
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def annualized_rate(self) -> float:
        """Annualized funding rate assuming 3x daily (8h intervals), in percent."""
        return self.funding_rate * 3 * 365 * 100


class FundingPosition(BaseModel):
    """Tracks a funding rate arbitrage position (spot long + perp short).

    Attributes:
        id: Unique position identifier.
        exchange: Exchange where position is held.
        symbol: Base symbol (e.g. "BTC/USDT").
        perp_symbol: Perpetual symbol (e.g. "BTC/USDT:USDT").
        status: Current position lifecycle status.
        quantity: Position size in base asset.
        spot_entry_price: Spot buy entry price.
        perp_entry_price: Perpetual short entry price.
        total_funding_collected: Cumulative funding payments received (USD).
        funding_payments: Count of funding settlement periods received.
        total_fees: Cumulative fees paid (entry + exit).
        opened_at: When the position was opened.
        closed_at: When the position was closed.
        last_funding_at: When last funding payment was collected.
        close_reason: Why the position was closed.
    """

    id: UUID = Field(default_factory=uuid4)
    exchange: str
    symbol: str
    perp_symbol: str
    status: FundingPositionStatus = FundingPositionStatus.PENDING
    quantity: float = 0.0
    spot_entry_price: float = 0.0
    perp_entry_price: float = 0.0
    total_funding_collected: float = 0.0
    funding_payments: int = 0
    total_fees: float = 0.0
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    last_funding_at: datetime | None = None
    close_reason: str | None = None

    @property
    def net_pnl(self) -> float:
        """Net PnL = funding collected - fees."""
        return self.total_funding_collected - self.total_fees

    @property
    def holding_hours(self) -> float:
        """Hours the position has been held."""
        if self.opened_at is None:
            return 0.0
        end = self.closed_at or datetime.now(UTC)
        return (end - self.opened_at).total_seconds() / 3600
