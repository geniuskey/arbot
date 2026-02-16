"""Abstract base class for trade execution engines."""

from abc import ABC, abstractmethod

from arbot.models.balance import PortfolioSnapshot
from arbot.models.signal import ArbitrageSignal
from arbot.models.trade import TradeResult


class BaseExecutor(ABC):
    """Abstract executor interface for arbitrage trade execution.

    Implementations handle the actual mechanics of placing orders
    (paper simulation or live exchange API calls).
    """

    @abstractmethod
    def execute(
        self, signal: ArbitrageSignal
    ) -> tuple[TradeResult, TradeResult]:
        """Execute an arbitrage signal as a buy + sell pair.

        Args:
            signal: The arbitrage signal to execute.

        Returns:
            Tuple of (buy_result, sell_result).

        Raises:
            InsufficientBalanceError: If balance is insufficient for the trade.
        """

    def execute_triangular(
        self, signal: ArbitrageSignal
    ) -> list[TradeResult]:
        """Execute a triangular arbitrage signal as a 3-leg trade.

        Args:
            signal: Triangular arbitrage signal with path/directions in metadata.

        Returns:
            List of 3 TradeResults, one per leg.
        """
        raise NotImplementedError("Triangular execution not supported")

    @abstractmethod
    def get_portfolio(self) -> PortfolioSnapshot:
        """Return a snapshot of the current portfolio state.

        Returns:
            PortfolioSnapshot with balances across all exchanges.
        """


class InsufficientBalanceError(Exception):
    """Raised when an exchange balance is insufficient to execute a trade."""

    def __init__(self, exchange: str, asset: str, required: float, available: float) -> None:
        self.exchange = exchange
        self.asset = asset
        self.required = required
        self.available = available
        super().__init__(
            f"Insufficient {asset} on {exchange}: "
            f"required={required:.8f}, available={available:.8f}"
        )
