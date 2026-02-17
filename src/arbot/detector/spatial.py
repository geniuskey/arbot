"""Spatial arbitrage detector for cross-exchange price discrepancies.

Compares order books across all exchange pairs to find profitable
arbitrage opportunities where the same asset trades at different
prices on different exchanges.
"""

from itertools import permutations

from arbot.detector.spread_calculator import ArbitrageProfit, SpreadCalculator
from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook
from arbot.models.signal import ArbitrageSignal, ArbitrageStrategy, SignalStatus


class SpatialDetector:
    """Detects spatial arbitrage opportunities across exchanges.

    Scans all directed exchange pairs (N exchanges -> N*(N-1) directions)
    and returns signals where net spread exceeds the minimum threshold
    and sufficient order book depth is available.

    Attributes:
        min_spread_pct: Minimum net spread percentage to consider profitable.
        min_depth_usd: Minimum available depth in USD on both sides.
        exchange_fees: Mapping of exchange name to its TradingFee schedule.
        default_quantity_usd: Default trade size in USD for evaluation.
    """

    def __init__(
        self,
        min_spread_pct: float = 0.25,
        min_depth_usd: float = 1000.0,
        exchange_fees: dict[str, TradingFee] | None = None,
        default_quantity_usd: float = 1000.0,
        use_gross_spread: bool = False,
    ) -> None:
        self.min_spread_pct = min_spread_pct
        self.min_depth_usd = min_depth_usd
        self.exchange_fees: dict[str, TradingFee] = exchange_fees or {}
        self.default_quantity_usd = default_quantity_usd
        self.use_gross_spread = use_gross_spread
        self._calc = SpreadCalculator()

    def detect(
        self,
        orderbooks: dict[str, OrderBook],
    ) -> list[ArbitrageSignal]:
        """Scan all exchange pairs for spatial arbitrage opportunities.

        Args:
            orderbooks: Mapping of exchange name to its current OrderBook.

        Returns:
            List of ArbitrageSignal sorted by net_spread_pct descending.
        """
        signals: list[ArbitrageSignal] = []
        exchanges = list(orderbooks.keys())
        quantity_usd = self.default_quantity_usd

        for buy_ex, sell_ex in permutations(exchanges, 2):
            buy_ob = orderbooks[buy_ex]
            sell_ob = orderbooks[sell_ex]

            buy_fee = self.exchange_fees.get(
                buy_ex, TradingFee(maker_pct=0.1, taker_pct=0.1)
            )
            sell_fee = self.exchange_fees.get(
                sell_ex, TradingFee(maker_pct=0.1, taker_pct=0.1)
            )

            signal = self._compare_pair(
                buy_ob, sell_ob, buy_fee, sell_fee, quantity_usd
            )
            if signal is not None:
                signals.append(signal)

        signals.sort(key=lambda s: s.net_spread_pct, reverse=True)
        return signals

    def _compare_pair(
        self,
        buy_ob: OrderBook,
        sell_ob: OrderBook,
        buy_fee: TradingFee,
        sell_fee: TradingFee,
        quantity_usd: float,
    ) -> ArbitrageSignal | None:
        """Compare a single directed exchange pair for arbitrage.

        Args:
            buy_ob: Order book of the exchange to buy on.
            sell_ob: Order book of the exchange to sell on.
            buy_fee: Fee schedule for the buy exchange.
            sell_fee: Fee schedule for the sell exchange.
            quantity_usd: Trade size in USD.

        Returns:
            ArbitrageSignal if the opportunity is profitable and has
            sufficient depth, otherwise None.
        """
        if not buy_ob.asks or not sell_ob.bids:
            return None

        # Sanity check: skip orderbooks with zero or negative best prices
        if buy_ob.asks[0].price <= 0 or sell_ob.bids[0].price <= 0:
            return None

        profit: ArbitrageProfit = self._calc.calculate_arbitrage_profit(
            buy_ob, sell_ob, buy_fee, sell_fee, quantity_usd, buy_maker=True
        )

        # Use gross spread for threshold when configured (useful for paper trading
        # where fees make net spread always negative on tier-1 exchanges)
        threshold_spread = profit.gross_spread_pct if self.use_gross_spread else profit.net_spread_pct

        if threshold_spread < self.min_spread_pct:
            return None

        if profit.available_depth_usd < self.min_depth_usd:
            return None

        if not self.use_gross_spread and profit.estimated_profit_usd <= 0:
            return None

        # Confidence based on how much spread exceeds the minimum
        # and how much depth is available relative to trade size
        spread_ratio = min(threshold_spread / self.min_spread_pct, 3.0) / 3.0
        depth_ratio = min(profit.available_depth_usd / quantity_usd, 10.0) / 10.0
        confidence = min((spread_ratio + depth_ratio) / 2, 1.0)

        # Quantity in base asset terms
        quantity = quantity_usd / profit.buy_effective_price if profit.buy_effective_price > 0 else 0.0

        return ArbitrageSignal(
            strategy=ArbitrageStrategy.SPATIAL,
            buy_exchange=buy_ob.exchange,
            sell_exchange=sell_ob.exchange,
            symbol=buy_ob.symbol,
            buy_price=profit.buy_effective_price,
            sell_price=profit.sell_effective_price,
            quantity=quantity,
            gross_spread_pct=profit.gross_spread_pct,
            net_spread_pct=profit.net_spread_pct,
            estimated_profit_usd=profit.estimated_profit_usd,
            confidence=confidence,
            orderbook_depth_usd=profit.available_depth_usd,
            status=SignalStatus.DETECTED,
            metadata={"buy_maker": True, "sell_maker": False},
        )
