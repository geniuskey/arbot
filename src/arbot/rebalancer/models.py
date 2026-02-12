"""Data models for rebalancing system."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class UrgencyLevel(Enum):
    """Urgency level for rebalance alerts."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ImbalanceAlert(BaseModel):
    """Alert for a detected balance imbalance on an exchange.

    Attributes:
        exchange: Exchange identifier.
        asset: Asset symbol (e.g. "USDT").
        current_pct: Current allocation percentage.
        target_pct: Target allocation percentage.
        deviation_pct: Absolute deviation from target.
        suggested_action: Human-readable suggested action.
    """

    model_config = {"frozen": True}

    exchange: str
    asset: str
    current_pct: float
    target_pct: float
    deviation_pct: float
    suggested_action: str


class NetworkInfo(BaseModel):
    """Information about a transfer network option.

    Attributes:
        network: Network identifier (e.g. "TRC20", "ERC20").
        fee: Transfer fee in asset units.
        estimated_minutes: Estimated transfer time in minutes.
        score: Composite score (higher is better).
    """

    model_config = {"frozen": True}

    network: str
    fee: float
    estimated_minutes: float
    score: float


class Transfer(BaseModel):
    """A single transfer between exchanges.

    Attributes:
        from_exchange: Source exchange.
        to_exchange: Destination exchange.
        asset: Asset to transfer.
        amount: Amount in asset units.
        network: Transfer network to use.
        estimated_fee: Estimated fee in asset units.
    """

    model_config = {"frozen": True}

    from_exchange: str
    to_exchange: str
    asset: str
    amount: float
    network: str
    estimated_fee: float


class RebalancePlan(BaseModel):
    """Plan for rebalancing funds across exchanges.

    Attributes:
        transfers: List of individual transfers.
        total_fee_estimate: Sum of all transfer fees (USD).
        estimated_duration_minutes: Estimated time for all transfers.
    """

    model_config = {"frozen": True}

    transfers: list[Transfer]
    total_fee_estimate: float
    estimated_duration_minutes: float


class RebalanceAlert(BaseModel):
    """Alert containing rebalance imbalances and suggested plan.

    Attributes:
        imbalances: List of detected imbalances.
        suggested_plan: Optimal rebalance plan, if computable.
        urgency: Urgency level based on deviation severity.
        message: Human-readable alert message.
    """

    imbalances: list[ImbalanceAlert]
    suggested_plan: RebalancePlan | None
    urgency: UrgencyLevel
    message: str
