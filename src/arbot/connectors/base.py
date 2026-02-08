"""Abstract base connector interface for exchange integrations.

Defines the BaseConnector ABC that all exchange-specific connectors must implement.
Includes connection state management, callback registration, and standard exchange
operations (order book subscription, trading, balance queries).
"""

import asyncio
import enum
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from arbot.logging import get_logger
from arbot.models import (
    AssetBalance,
    ExchangeInfo,
    Order,
    OrderBook,
    OrderSide,
    OrderType,
    TradingFee,
    TradeResult,
)


class ConnectionState(enum.Enum):
    """WebSocket connection state."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


class BaseConnector(ABC):
    """Abstract exchange connector interface.

    Provides a unified API for interacting with cryptocurrency exchanges,
    including WebSocket data streaming and REST API order management.

    Args:
        exchange_name: Exchange identifier (e.g. "binance").
        config: Exchange configuration including fees and rate limits.
    """

    def __init__(self, exchange_name: str, config: ExchangeInfo) -> None:
        self.exchange_name = exchange_name
        self.config = config
        self.state = ConnectionState.DISCONNECTED
        self._orderbook_callbacks: list[Callable[[OrderBook], Awaitable[None]]] = []
        self._trade_callbacks: list[Callable[[TradeResult], Awaitable[None]]] = []
        self._logger = get_logger(f"connector.{exchange_name}")

    # --- Connection Management ---

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the exchange (WebSocket and/or REST).

        Raises:
            ConnectionError: If the connection cannot be established.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close all connections to the exchange."""

    @property
    def is_connected(self) -> bool:
        """Whether the connector is currently connected."""
        return self.state == ConnectionState.CONNECTED

    # --- Data Subscription ---

    @abstractmethod
    async def subscribe_orderbook(self, symbols: list[str], depth: int = 10) -> None:
        """Subscribe to real-time order book updates.

        Args:
            symbols: Trading pairs to subscribe (e.g. ["BTC/USDT", "ETH/USDT"]).
            depth: Number of price levels to receive.
        """

    @abstractmethod
    async def subscribe_trades(self, symbols: list[str]) -> None:
        """Subscribe to real-time trade (execution) stream.

        Args:
            symbols: Trading pairs to subscribe.
        """

    # --- Callback Registration ---

    def on_orderbook_update(
        self, callback: Callable[[OrderBook], Awaitable[None]]
    ) -> None:
        """Register a callback for order book updates.

        Args:
            callback: Async function called with each OrderBook update.
        """
        self._orderbook_callbacks.append(callback)
        self._logger.debug("orderbook_callback_registered", total=len(self._orderbook_callbacks))

    def on_trade_update(
        self, callback: Callable[[TradeResult], Awaitable[None]]
    ) -> None:
        """Register a callback for trade execution updates.

        Args:
            callback: Async function called with each TradeResult update.
        """
        self._trade_callbacks.append(callback)
        self._logger.debug("trade_callback_registered", total=len(self._trade_callbacks))

    # --- Callback Dispatching ---

    async def _notify_orderbook(self, orderbook: OrderBook) -> None:
        """Dispatch an order book update to all registered callbacks.

        Args:
            orderbook: The updated order book snapshot.
        """
        for callback in self._orderbook_callbacks:
            try:
                await callback(orderbook)
            except Exception:
                self._logger.exception(
                    "orderbook_callback_error",
                    exchange=self.exchange_name,
                    symbol=orderbook.symbol,
                )

    async def _notify_trade(self, trade: TradeResult) -> None:
        """Dispatch a trade update to all registered callbacks.

        Args:
            trade: The trade execution result.
        """
        for callback in self._trade_callbacks:
            try:
                await callback(trade)
            except Exception:
                self._logger.exception(
                    "trade_callback_error",
                    exchange=self.exchange_name,
                )

    # --- State Management ---

    def _set_state(self, new_state: ConnectionState) -> None:
        """Update connection state with logging.

        Args:
            new_state: The new connection state.
        """
        old_state = self.state
        self.state = new_state
        self._logger.info(
            "connection_state_changed",
            exchange=self.exchange_name,
            old_state=old_state.value,
            new_state=new_state.value,
        )

    # --- Trading ---

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
    ) -> Order:
        """Place a new order on the exchange.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT").
            side: Buy or sell.
            order_type: Order type (limit, market, IOC).
            quantity: Order quantity in base asset.
            price: Limit price. Required for LIMIT/IOC orders, ignored for MARKET.

        Returns:
            The created Order with exchange-assigned ID and initial status.

        Raises:
            ValueError: If required parameters are missing (e.g. price for LIMIT order).
        """

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order.

        Args:
            order_id: Exchange order ID.
            symbol: Trading pair the order belongs to.

        Returns:
            True if the order was successfully cancelled, False otherwise.
        """

    @abstractmethod
    async def get_order_status(self, order_id: str, symbol: str) -> Order:
        """Query the current status of an order.

        Args:
            order_id: Exchange order ID.
            symbol: Trading pair the order belongs to.

        Returns:
            The Order with up-to-date status and fill information.
        """

    # --- Account Information ---

    @abstractmethod
    async def get_balances(self) -> dict[str, AssetBalance]:
        """Query account balances for all assets.

        Returns:
            Mapping of asset symbol to AssetBalance.
        """

    @abstractmethod
    async def get_trading_fee(self, symbol: str) -> TradingFee:
        """Query the trading fee for a specific symbol.

        Args:
            symbol: Trading pair.

        Returns:
            TradingFee with maker and taker rates.
        """

    @abstractmethod
    async def get_withdrawal_fee(self, asset: str, network: str) -> float:
        """Query the withdrawal fee for an asset on a specific network.

        Args:
            asset: Asset symbol (e.g. "USDT").
            network: Transfer network (e.g. "TRC20", "ERC20").

        Returns:
            Withdrawal fee amount in the asset's unit.
        """

    # --- Context Manager ---

    async def __aenter__(self) -> "BaseConnector":
        """Async context manager entry: connect to the exchange."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: object) -> None:
        """Async context manager exit: disconnect from the exchange."""
        await self.disconnect()
