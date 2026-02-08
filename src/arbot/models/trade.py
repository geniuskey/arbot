"""Trade-related data models for orders and execution results."""

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class OrderSide(str, enum.Enum):
    """Order side."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    """Order type."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"
    IOC = "IOC"


class OrderStatus(str, enum.Enum):
    """Order lifecycle status."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ExecutionMode(str, enum.Enum):
    """Trading execution mode."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class Order(BaseModel):
    """Represents a trading order.

    Attributes:
        id: Unique order identifier (UUID).
        exchange: Exchange where the order is placed.
        symbol: Trading pair (e.g. "BTC/USDT").
        side: Buy or sell.
        order_type: Order type (limit, market, IOC).
        quantity: Order quantity.
        price: Limit price (None for market orders).
        status: Current order status.
        created_at: Timestamp when the order was created.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    exchange: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TradeResult(BaseModel):
    """Result of an executed trade.

    Attributes:
        order: The original order.
        filled_quantity: Actual filled quantity.
        filled_price: Actual average fill price.
        fee: Trading fee amount.
        fee_asset: Asset in which the fee was charged.
        latency_ms: Execution latency in milliseconds.
        filled_at: Timestamp when the fill occurred.
    """

    model_config = {"frozen": True}

    order: Order
    filled_quantity: float
    filled_price: float
    fee: float
    fee_asset: str
    latency_ms: float
    filled_at: datetime = Field(default_factory=datetime.utcnow)
