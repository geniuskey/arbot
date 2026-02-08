"""Data models (Pydantic).

Re-exports all core data models for convenient imports:

    from arbot.models import OrderBook, ArbitrageSignal, ExchangeBalance
"""

from arbot.models.balance import AssetBalance, ExchangeBalance, PortfolioSnapshot
from arbot.models.config import ExchangeInfo, RiskConfig, TradingFee
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.models.signal import (
    ArbitrageSignal,
    ArbitrageStrategy,
    SignalStatus,
)
from arbot.models.trade import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TradeResult,
)

__all__ = [
    "ArbitrageSignal",
    "ArbitrageStrategy",
    "AssetBalance",
    "ExchangeBalance",
    "ExchangeInfo",
    "ExecutionMode",
    "Order",
    "OrderBook",
    "OrderBookEntry",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PortfolioSnapshot",
    "RiskConfig",
    "SignalStatus",
    "TradingFee",
    "TradeResult",
]
