"""OrderBook data models for exchange order book representation."""

from pydantic import BaseModel, Field


class OrderBookEntry(BaseModel):
    """Single price level in an order book.

    Attributes:
        price: Price at this level.
        quantity: Available quantity at this price.
    """

    model_config = {"frozen": True}

    price: float
    quantity: float


class OrderBook(BaseModel):
    """Exchange order book snapshot.

    Bids are sorted in descending price order (best bid first).
    Asks are sorted in ascending price order (best ask first).

    Attributes:
        exchange: Exchange identifier (e.g. "binance").
        symbol: Trading pair (e.g. "BTC/USDT").
        timestamp: Unix timestamp of the snapshot.
        bids: List of bid entries, sorted by price descending.
        asks: List of ask entries, sorted by price ascending.
    """

    model_config = {"frozen": True}

    exchange: str
    symbol: str
    timestamp: float
    bids: list[OrderBookEntry] = Field(default_factory=list)
    asks: list[OrderBookEntry] = Field(default_factory=list)

    @property
    def best_bid(self) -> float:
        """Highest bid price."""
        if not self.bids:
            return 0.0
        return self.bids[0].price

    @property
    def best_ask(self) -> float:
        """Lowest ask price."""
        if not self.asks:
            return 0.0
        return self.asks[0].price

    @property
    def mid_price(self) -> float:
        """Mid price between best bid and best ask."""
        if not self.bids or not self.asks:
            return 0.0
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float:
        """Absolute spread between best ask and best bid."""
        if not self.bids or not self.asks:
            return 0.0
        return self.best_ask - self.best_bid

    @property
    def spread_pct(self) -> float:
        """Spread as a percentage of mid price."""
        mid = self.mid_price
        if mid == 0.0:
            return 0.0
        return (self.spread / mid) * 100

    def depth_at_price(self, side: str, depth_usd: float) -> float:
        """Calculate volume-weighted average price up to a given USD depth.

        Args:
            side: "bid" or "ask".
            depth_usd: Maximum USD amount to consume from the book.

        Returns:
            Volume-weighted average price for the requested depth.
            Returns 0.0 if the book is empty or depth_usd is non-positive.
        """
        if depth_usd <= 0:
            return 0.0

        entries = self.bids if side == "bid" else self.asks
        if not entries:
            return 0.0

        remaining_usd = depth_usd
        total_qty = 0.0
        total_cost = 0.0

        for entry in entries:
            entry_usd = entry.price * entry.quantity
            if entry_usd <= remaining_usd:
                total_qty += entry.quantity
                total_cost += entry_usd
                remaining_usd -= entry_usd
            else:
                partial_qty = remaining_usd / entry.price
                total_qty += partial_qty
                total_cost += remaining_usd
                remaining_usd = 0.0
                break

        if total_qty == 0.0:
            return 0.0
        return total_cost / total_qty
