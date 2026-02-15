"""Balance and portfolio data models."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class AssetBalance(BaseModel):
    """Balance for a single asset on an exchange.

    Attributes:
        asset: Asset symbol (e.g. "BTC", "USDT").
        free: Available balance for trading.
        locked: Balance locked in open orders.
        usd_value: Estimated USD value of total balance.
    """

    asset: str
    free: float = 0.0
    locked: float = 0.0
    usd_value: float | None = None

    @property
    def total(self) -> float:
        """Total balance (free + locked)."""
        return self.free + self.locked


class ExchangeBalance(BaseModel):
    """Aggregated balances for a single exchange.

    Attributes:
        exchange: Exchange identifier.
        balances: Mapping of asset symbol to balance.
        updated_at: Timestamp of the last balance update.
    """

    exchange: str
    balances: dict[str, AssetBalance] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def total_usd_value(self) -> float:
        """Sum of all asset USD values on this exchange."""
        total = 0.0
        for bal in self.balances.values():
            if bal.usd_value is not None:
                total += bal.usd_value
        return total


class PortfolioSnapshot(BaseModel):
    """Point-in-time snapshot of the entire portfolio across exchanges.

    Attributes:
        timestamp: Snapshot timestamp.
        exchange_balances: Mapping of exchange name to balance.
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    exchange_balances: dict[str, ExchangeBalance] = Field(default_factory=dict)

    @property
    def total_usd_value(self) -> float:
        """Total portfolio USD value across all exchanges."""
        return sum(eb.total_usd_value for eb in self.exchange_balances.values())

    @property
    def allocation_by_exchange(self) -> dict[str, float]:
        """Percentage allocation by exchange.

        Returns:
            Dict mapping exchange name to its percentage of total portfolio value.
            Returns empty dict if total value is zero.
        """
        total = self.total_usd_value
        if total == 0.0:
            return {}
        return {
            name: (eb.total_usd_value / total) * 100
            for name, eb in self.exchange_balances.items()
        }
