"""Triangular arbitrage detector for single-exchange price discrepancies.

Finds profitable three-leg trading paths within a single exchange
where currency conversion through an intermediate asset yields
more than a direct conversion.

Example path: USDT -> BTC -> ETH -> USDT
  Leg 1: Buy BTC/USDT (spend USDT, get BTC)
  Leg 2: Buy ETH/BTC  (spend BTC, get ETH)
  Leg 3: Sell ETH/USDT (spend ETH, get USDT)
  Profit if final USDT > initial USDT after fees.
"""

from itertools import combinations

from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook
from arbot.models.signal import ArbitrageSignal, ArbitrageStrategy, SignalStatus


class TriangularDetector:
    """Detects triangular arbitrage opportunities within a single exchange.

    Attributes:
        min_profit_pct: Minimum net profit percentage to report a signal.
        default_fee: Trading fee applied to each leg of the triangle.
    """

    def __init__(
        self,
        min_profit_pct: float = 0.15,
        default_fee: TradingFee | None = None,
    ) -> None:
        self.min_profit_pct = min_profit_pct
        self.default_fee = default_fee or TradingFee(maker_pct=0.1, taker_pct=0.1)

    def detect(
        self,
        orderbooks: dict[str, OrderBook],
        exchange: str,
        quantity_usd: float = 1000.0,
    ) -> list[ArbitrageSignal]:
        """Scan for triangular arbitrage opportunities on a single exchange.

        Args:
            orderbooks: Mapping of symbol to OrderBook for the exchange.
            exchange: Exchange name.
            quantity_usd: Starting amount in USD to simulate.

        Returns:
            List of ArbitrageSignal sorted by net_spread_pct descending.
        """
        symbols = list(orderbooks.keys())
        paths = self._find_triangular_paths(symbols)
        signals: list[ArbitrageSignal] = []

        for path in paths:
            signal = self._calculate_path_profit(
                path, orderbooks, self.default_fee, quantity_usd, exchange
            )
            if signal is not None:
                signals.append(signal)

        signals.sort(key=lambda s: s.net_spread_pct, reverse=True)
        return signals

    @staticmethod
    def _parse_pair(symbol: str) -> tuple[str, str]:
        """Parse 'BASE/QUOTE' into (base, quote)."""
        parts = symbol.split("/")
        return parts[0], parts[1]

    def _find_triangular_paths(
        self, symbols: list[str]
    ) -> list[tuple[str, str, str]]:
        """Find all valid triangular trading paths from available symbols.

        A valid triangle consists of three symbols that share assets
        forming a cycle. For example, with assets A, B, C:
        A/B, B/C, A/C form a triangle (A -> B -> C -> A).

        Args:
            symbols: List of trading pair symbols (e.g. ["BTC/USDT", "ETH/BTC", "ETH/USDT"]).

        Returns:
            List of 3-tuples of symbols forming valid triangular paths.
        """
        paths: list[tuple[str, str, str]] = []

        # Build adjacency: map each asset to the symbols it appears in
        asset_symbols: dict[str, list[str]] = {}
        for sym in symbols:
            base, quote = self._parse_pair(sym)
            asset_symbols.setdefault(base, []).append(sym)
            asset_symbols.setdefault(quote, []).append(sym)

        # Check all 3-symbol combinations
        for combo in combinations(symbols, 3):
            # Collect all assets involved
            assets: set[str] = set()
            for sym in combo:
                base, quote = self._parse_pair(sym)
                assets.add(base)
                assets.add(quote)

            # A valid triangle has exactly 3 distinct assets
            # and each asset appears in exactly 2 of the 3 symbols
            if len(assets) != 3:
                continue

            asset_count: dict[str, int] = {a: 0 for a in assets}
            for sym in combo:
                base, quote = self._parse_pair(sym)
                asset_count[base] += 1
                asset_count[quote] += 1

            if all(c == 2 for c in asset_count.values()):
                paths.append(combo)

        return paths

    def _calculate_path_profit(
        self,
        path: tuple[str, str, str],
        orderbooks: dict[str, OrderBook],
        fee: TradingFee,
        quantity_usd: float,
        exchange: str,
    ) -> ArbitrageSignal | None:
        """Simulate executing a triangular path and calculate profit.

        Starting with quantity_usd of a stable asset, traverse each leg
        buying or selling as appropriate, applying fees at each step.

        Args:
            path: Tuple of 3 symbols forming the triangular path.
            orderbooks: Mapping of symbol to OrderBook.
            fee: Trading fee to apply at each leg.
            quantity_usd: Starting amount in USD.
            exchange: Exchange name.

        Returns:
            ArbitrageSignal if profitable above threshold, else None.
        """
        # Determine the traversal order and directions
        # Find the 3 distinct assets
        assets: set[str] = set()
        for sym in path:
            base, quote = self._parse_pair(sym)
            assets.add(base)
            assets.add(quote)

        if len(assets) != 3:
            return None

        # Find a stable/quote asset to start from (prefer USDT, USDC, USD, BTC)
        start_asset = self._pick_start_asset(assets)

        # Build all valid cycles and try both directions
        cycles = self._build_all_cycles(path, start_asset)
        if not cycles:
            return None

        # Try each cycle direction, keep the most profitable
        best_result: tuple[float, float, float, float, list[tuple[str, str]]] | None = None

        for cycle in cycles:
            result = self._simulate_cycle(cycle, orderbooks, fee.taker_pct, quantity_usd)
            if result is None:
                continue
            final_amount, min_depth, current_cycle = result
            net_pct = ((final_amount / quantity_usd) - 1) * 100
            if best_result is None or net_pct > best_result[0]:
                gross_pct = net_pct + fee.taker_pct * 3
                profit_usd = final_amount - quantity_usd
                best_result = (net_pct, gross_pct, profit_usd, min_depth, current_cycle)

        if best_result is None:
            return None

        net_pct, gross_pct, profit_usd, min_depth_usd, cycle = best_result

        if net_pct < self.min_profit_pct:
            return None

        if profit_usd <= 0:
            return None

        # Confidence: higher for bigger profit margin above threshold
        profit_ratio = min(net_pct / self.min_profit_pct, 3.0) / 3.0
        confidence = min(profit_ratio, 1.0)

        path_symbols = [s for s, _ in cycle]
        first_ob = orderbooks[path_symbols[0]]
        last_ob = orderbooks[path_symbols[-1]]

        return ArbitrageSignal(
            strategy=ArbitrageStrategy.TRIANGULAR,
            buy_exchange=exchange,
            sell_exchange=exchange,
            symbol=path_symbols[0],
            buy_price=first_ob.best_ask,
            sell_price=last_ob.best_bid,
            quantity=quantity_usd / first_ob.best_ask if first_ob.best_ask > 0 else 0.0,
            gross_spread_pct=gross_pct,
            net_spread_pct=net_pct,
            estimated_profit_usd=profit_usd,
            confidence=confidence,
            orderbook_depth_usd=min_depth_usd if min_depth_usd != float("inf") else 0.0,
            status=SignalStatus.DETECTED,
            metadata={"path": list(path), "directions": [d for _, d in cycle]},
        )

    @staticmethod
    def _pick_start_asset(assets: set[str]) -> str:
        """Pick the best starting asset for the cycle, preferring stablecoins."""
        for preferred in ["USDT", "USDC", "BUSD", "USD", "DAI"]:
            if preferred in assets:
                return preferred
        # Fallback: pick alphabetically first
        return sorted(assets)[0]

    def _simulate_cycle(
        self,
        cycle: list[tuple[str, str]],
        orderbooks: dict[str, OrderBook],
        fee_pct: float,
        quantity_usd: float,
    ) -> tuple[float, float, list[tuple[str, str]]] | None:
        """Simulate a single cycle direction and return the result.

        Returns:
            Tuple of (final_amount, min_depth_usd, cycle) or None if invalid.
        """
        current_amount = quantity_usd
        min_depth_usd = float("inf")

        for symbol, direction in cycle:
            ob = orderbooks.get(symbol)
            if ob is None or not ob.bids or not ob.asks:
                return None

            if direction == "buy":
                price = ob.best_ask
                if price <= 0:
                    return None
                received = current_amount / price
                received *= (1 - fee_pct / 100)
                current_amount = received
                depth = sum(e.price * e.quantity for e in ob.asks)
                min_depth_usd = min(min_depth_usd, depth)
            else:
                price = ob.best_bid
                if price <= 0:
                    return None
                received = current_amount * price
                received *= (1 - fee_pct / 100)
                current_amount = received
                depth = sum(e.price * e.quantity for e in ob.bids)
                min_depth_usd = min(min_depth_usd, depth)

        return current_amount, min_depth_usd, cycle

    def _build_all_cycles(
        self,
        path: tuple[str, str, str],
        start_asset: str,
    ) -> list[list[tuple[str, str]]]:
        """Build all valid cycle orderings for the path starting from start_asset.

        Returns both clockwise and counter-clockwise directions.

        Args:
            path: Tuple of 3 symbols.
            start_asset: Asset to start and end with.

        Returns:
            List of valid cycles, each being a list of (symbol, direction) tuples.
        """
        edges: dict[tuple[str, str], tuple[str, str]] = {}
        for sym in path:
            base, quote = self._parse_pair(sym)
            edges[(quote, base)] = (sym, "buy")
            edges[(base, quote)] = (sym, "sell")

        all_assets: set[str] = set()
        for sym in path:
            base, quote = self._parse_pair(sym)
            all_assets.add(base)
            all_assets.add(quote)

        other_assets = all_assets - {start_asset}
        if len(other_assets) != 2:
            return []

        cycles: list[list[tuple[str, str]]] = []
        others = sorted(other_assets)  # deterministic ordering

        for mid_asset in others:
            end_asset = (other_assets - {mid_asset}).pop()
            leg1 = edges.get((start_asset, mid_asset))
            leg2 = edges.get((mid_asset, end_asset))
            leg3 = edges.get((end_asset, start_asset))
            if leg1 and leg2 and leg3:
                cycles.append([leg1, leg2, leg3])

        return cycles
