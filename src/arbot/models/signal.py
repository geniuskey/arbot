"""Arbitrage signal data models."""

import enum
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ArbitrageStrategy(str, enum.Enum):
    """Type of arbitrage strategy."""

    SPATIAL = "SPATIAL"
    TRIANGULAR = "TRIANGULAR"
    STATISTICAL = "STATISTICAL"
    FUNDING_RATE = "FUNDING_RATE"


class SignalStatus(str, enum.Enum):
    """Lifecycle status of an arbitrage signal."""

    DETECTED = "DETECTED"
    EXECUTED = "EXECUTED"
    MISSED = "MISSED"
    REJECTED = "REJECTED"


class ArbitrageSignal(BaseModel):
    """Represents a detected arbitrage opportunity.

    Attributes:
        id: Unique signal identifier.
        strategy: Arbitrage strategy type.
        buy_exchange: Exchange to buy on.
        sell_exchange: Exchange to sell on.
        symbol: Trading pair (e.g. "BTC/USDT").
        buy_price: Best available buy price.
        sell_price: Best available sell price.
        quantity: Recommended trade quantity.
        gross_spread_pct: Gross spread percentage before fees.
        net_spread_pct: Net spread percentage after fees.
        estimated_profit_usd: Estimated profit in USD.
        confidence: Signal confidence score (0 to 1).
        orderbook_depth_usd: Available liquidity in USD at the signal price.
        status: Current signal status.
        detected_at: Timestamp when the signal was detected.
        executed_at: Timestamp when the signal was executed (if applicable).
        metadata: Additional strategy-specific metadata.
    """

    id: UUID = Field(default_factory=uuid4)
    strategy: ArbitrageStrategy
    buy_exchange: str
    sell_exchange: str
    symbol: str
    buy_price: float
    sell_price: float
    quantity: float
    gross_spread_pct: float
    net_spread_pct: float
    estimated_profit_usd: float
    confidence: float = Field(ge=0.0, le=1.0)
    orderbook_depth_usd: float
    status: SignalStatus = SignalStatus.DETECTED
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    executed_at: datetime | None = None
    metadata: dict[str, Any] | None = None
