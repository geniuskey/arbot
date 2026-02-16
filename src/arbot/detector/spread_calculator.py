"""Spread calculator for arbitrage profit estimation.

Pure computation module with no external dependencies (no Redis, DB, etc.).
Uses OrderBook and TradingFee models from arbot.models.
"""

from pydantic import BaseModel

from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook
from arbot.models.trade import OrderSide


class ArbitrageProfit(BaseModel):
    """Result of an arbitrage profit calculation.

    Attributes:
        buy_effective_price: Volume-weighted average buy price from the order book.
        sell_effective_price: Volume-weighted average sell price from the order book.
        gross_spread_pct: Spread percentage before fees.
        net_spread_pct: Spread percentage after fees.
        estimated_profit_usd: Estimated net profit in USD for the given quantity.
        available_depth_usd: Minimum available depth across buy and sell books.
        is_profitable: Whether the opportunity has positive net spread and profit.
    """

    model_config = {"frozen": True}

    buy_effective_price: float
    sell_effective_price: float
    gross_spread_pct: float
    net_spread_pct: float
    estimated_profit_usd: float
    available_depth_usd: float
    is_profitable: bool


class SpreadCalculator:
    """Calculator for cross-exchange arbitrage spread and profit.

    All methods are stateless. The class groups related calculations
    without holding mutable state.
    """

    @staticmethod
    def calculate_gross_spread(buy_price: float, sell_price: float) -> float:
        """Calculate gross spread as a percentage.

        Args:
            buy_price: Price to buy at (lower exchange ask).
            sell_price: Price to sell at (higher exchange bid).

        Returns:
            Gross spread percentage. Positive when sell > buy.
        """
        if buy_price <= 0:
            return 0.0
        return ((sell_price - buy_price) / buy_price) * 100

    @staticmethod
    def calculate_net_spread(
        buy_price: float,
        sell_price: float,
        buy_fee_pct: float,
        sell_fee_pct: float,
    ) -> float:
        """Calculate net spread after deducting trading fees.

        Args:
            buy_price: Price to buy at.
            sell_price: Price to sell at.
            buy_fee_pct: Fee percentage on the buy side (e.g. 0.1 for 0.1%).
            sell_fee_pct: Fee percentage on the sell side (e.g. 0.1 for 0.1%).

        Returns:
            Net spread percentage after fees.
        """
        if buy_price <= 0:
            return 0.0
        gross_pct = ((sell_price - buy_price) / buy_price) * 100
        return gross_pct - buy_fee_pct - sell_fee_pct

    @staticmethod
    def calculate_effective_price(
        orderbook: OrderBook,
        side: OrderSide,
        quantity_usd: float,
    ) -> float:
        """Calculate volume-weighted average execution price from the order book.

        For a BUY, we consume the ask side (ascending prices).
        For a SELL, we consume the bid side (descending prices).

        Args:
            orderbook: The exchange order book.
            side: BUY or SELL.
            quantity_usd: Amount in USD to fill.

        Returns:
            Volume-weighted average price. Returns 0.0 if the book is
            empty or quantity_usd is non-positive.
        """
        if quantity_usd <= 0:
            return 0.0
        book_side = "ask" if side == OrderSide.BUY else "bid"
        return orderbook.depth_at_price(book_side, quantity_usd)

    @staticmethod
    def _available_depth(orderbook: OrderBook, side: str) -> float:
        """Sum total USD depth available on a given side of the book."""
        entries = orderbook.asks if side == "ask" else orderbook.bids
        return sum(e.price * e.quantity for e in entries)

    def calculate_arbitrage_profit(
        self,
        buy_ob: OrderBook,
        sell_ob: OrderBook,
        buy_fee: TradingFee,
        sell_fee: TradingFee,
        quantity_usd: float,
        buy_maker: bool = True,
    ) -> ArbitrageProfit:
        """Calculate comprehensive arbitrage profit for a given trade size.

        Buys on `buy_ob` (consuming asks) and sells on `sell_ob`
        (consuming bids), accounting for order book depth and fees.

        Args:
            buy_ob: Order book of the exchange to buy on.
            sell_ob: Order book of the exchange to sell on.
            buy_fee: Fee schedule for the buy exchange.
            sell_fee: Fee schedule for the sell exchange.
            quantity_usd: Trade size in USD.
            buy_maker: If True, use maker fee for buy side (limit order).

        Returns:
            ArbitrageProfit with all computed fields.
        """
        buy_eff = self.calculate_effective_price(buy_ob, OrderSide.BUY, quantity_usd)
        sell_eff = self.calculate_effective_price(sell_ob, OrderSide.SELL, quantity_usd)

        gross_pct = self.calculate_gross_spread(buy_eff, sell_eff)
        buy_fee_pct = buy_fee.maker_pct if buy_maker else buy_fee.taker_pct
        net_pct = self.calculate_net_spread(
            buy_eff, sell_eff, buy_fee_pct, sell_fee.taker_pct
        )

        # Estimated profit: net spread applied to the trade size
        estimated_profit = (net_pct / 100) * quantity_usd if buy_eff > 0 else 0.0

        buy_depth = self._available_depth(buy_ob, "ask")
        sell_depth = self._available_depth(sell_ob, "bid")
        available_depth = min(buy_depth, sell_depth)

        return ArbitrageProfit(
            buy_effective_price=buy_eff,
            sell_effective_price=sell_eff,
            gross_spread_pct=gross_pct,
            net_spread_pct=net_pct,
            estimated_profit_usd=estimated_profit,
            available_depth_usd=available_depth,
            is_profitable=net_pct > 0 and estimated_profit > 0,
        )

    @staticmethod
    def is_profitable(profit: ArbitrageProfit, min_spread_pct: float) -> bool:
        """Check whether an arbitrage opportunity meets the minimum profitability threshold.

        Args:
            profit: Computed arbitrage profit.
            min_spread_pct: Minimum required net spread percentage.

        Returns:
            True if net_spread_pct >= min_spread_pct and estimated_profit > 0.
        """
        return profit.net_spread_pct >= min_spread_pct and profit.estimated_profit_usd > 0
