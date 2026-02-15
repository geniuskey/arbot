"""Paper trading executor with simulated fills and virtual portfolio.

Simulates trade execution against real order book data without
placing actual orders. Maintains virtual balances and tracks
all trades for PnL calculation.
"""

from datetime import UTC, datetime

from arbot.execution.base import BaseExecutor, InsufficientBalanceError
from arbot.execution.fill_simulator import FillSimulator
from arbot.models.balance import AssetBalance, ExchangeBalance, PortfolioSnapshot
from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook
from arbot.models.signal import ArbitrageSignal
from arbot.models.trade import OrderSide, TradeResult


class PaperExecutor(BaseExecutor):
    """Paper trading executor that simulates fills with virtual balances.

    Attributes:
        balances: Virtual balances per exchange per asset.
        exchange_fees: Fee schedule per exchange.
        orderbooks: Latest order books keyed by "exchange:symbol".
        trade_history: List of executed (buy_result, sell_result) pairs.
        initial_balances: Snapshot of initial balances for PnL calculation.
    """

    def __init__(
        self,
        initial_balances: dict[str, dict[str, float]],
        exchange_fees: dict[str, TradingFee],
    ) -> None:
        """Initialize the paper executor.

        Args:
            initial_balances: Mapping of exchange -> {asset: amount}.
                Example: {"binance": {"USDT": 10000, "BTC": 0.1}}
            exchange_fees: Mapping of exchange name to TradingFee.
        """
        self.balances: dict[str, dict[str, float]] = {}
        self.initial_balances: dict[str, dict[str, float]] = {}
        for exchange, assets in initial_balances.items():
            self.balances[exchange] = dict(assets)
            self.initial_balances[exchange] = dict(assets)

        self.exchange_fees = exchange_fees
        self.orderbooks: dict[str, OrderBook] = {}
        self.trade_history: list[tuple[TradeResult, TradeResult]] = []
        self._simulator = FillSimulator()

    def update_orderbooks(self, orderbooks: dict[str, OrderBook]) -> None:
        """Update cached order books.

        Args:
            orderbooks: Mapping of "exchange:symbol" to OrderBook.
        """
        self.orderbooks.update(orderbooks)

    def execute(
        self, signal: ArbitrageSignal
    ) -> tuple[TradeResult, TradeResult]:
        """Execute an arbitrage signal as simulated buy + sell.

        Buys on signal.buy_exchange and sells on signal.sell_exchange.
        Updates virtual balances accordingly.

        Args:
            signal: The arbitrage signal to execute.

        Returns:
            Tuple of (buy_result, sell_result).

        Raises:
            InsufficientBalanceError: If balance is insufficient.
            ValueError: If required order book is not available.
        """
        symbol = signal.symbol
        base_asset, quote_asset = symbol.split("/")
        buy_ex = signal.buy_exchange
        sell_ex = signal.sell_exchange

        # Get order books
        buy_ob_key = f"{buy_ex}:{symbol}"
        sell_ob_key = f"{sell_ex}:{symbol}"

        buy_ob = self.orderbooks.get(buy_ob_key)
        sell_ob = self.orderbooks.get(sell_ob_key)
        if buy_ob is None or sell_ob is None:
            raise ValueError(
                f"Missing orderbook: buy={buy_ob_key} sell={sell_ob_key}"
            )

        buy_fee = self.exchange_fees.get(
            buy_ex, TradingFee(maker_pct=0.1, taker_pct=0.1)
        )
        sell_fee = self.exchange_fees.get(
            sell_ex, TradingFee(maker_pct=0.1, taker_pct=0.1)
        )

        quantity = signal.quantity

        # Check buy side: need quote asset on buy exchange
        quote_needed = quantity * signal.buy_price
        buy_balance = self._get_balance(buy_ex, quote_asset)
        if buy_balance < quote_needed:
            raise InsufficientBalanceError(
                buy_ex, quote_asset, quote_needed, buy_balance
            )

        # Check sell side: need base asset on sell exchange
        sell_balance = self._get_balance(sell_ex, base_asset)
        if sell_balance < quantity:
            raise InsufficientBalanceError(
                sell_ex, base_asset, quantity, sell_balance
            )

        # Simulate fills
        buy_result = self._simulator.simulate_fill(
            buy_ob, OrderSide.BUY, quantity, buy_fee
        )
        sell_result = self._simulator.simulate_fill(
            sell_ob, OrderSide.SELL, quantity, sell_fee
        )

        # Update balances for the buy side
        buy_cost = buy_result.filled_quantity * buy_result.filled_price
        self._adjust_balance(buy_ex, quote_asset, -buy_cost)
        received_base = buy_result.filled_quantity - buy_result.fee
        self._adjust_balance(buy_ex, base_asset, received_base)

        # Update balances for the sell side
        self._adjust_balance(sell_ex, base_asset, -sell_result.filled_quantity)
        sell_proceeds = sell_result.filled_quantity * sell_result.filled_price
        received_quote = sell_proceeds - sell_result.fee
        self._adjust_balance(sell_ex, quote_asset, received_quote)

        self.trade_history.append((buy_result, sell_result))
        return buy_result, sell_result

    def get_portfolio(self) -> PortfolioSnapshot:
        """Return current virtual portfolio snapshot.

        Returns:
            PortfolioSnapshot with all exchange balances.
        """
        exchange_balances: dict[str, ExchangeBalance] = {}
        for exchange, assets in self.balances.items():
            asset_balances: dict[str, AssetBalance] = {}
            for asset, amount in assets.items():
                asset_balances[asset] = AssetBalance(
                    asset=asset,
                    free=amount,
                    locked=0.0,
                )
            exchange_balances[exchange] = ExchangeBalance(
                exchange=exchange,
                balances=asset_balances,
            )

        return PortfolioSnapshot(
            timestamp=datetime.now(UTC),
            exchange_balances=exchange_balances,
        )

    def get_trade_history(self) -> list[tuple[TradeResult, TradeResult]]:
        """Return all executed trade pairs.

        Returns:
            List of (buy_result, sell_result) tuples.
        """
        return list(self.trade_history)

    def get_pnl(self) -> dict[str, dict[str, float]]:
        """Calculate profit/loss per exchange per asset vs initial balances.

        Returns:
            Mapping of exchange -> {asset: pnl_amount}.
        """
        pnl: dict[str, dict[str, float]] = {}
        all_exchanges = set(self.balances.keys()) | set(self.initial_balances.keys())

        for exchange in all_exchanges:
            current = self.balances.get(exchange, {})
            initial = self.initial_balances.get(exchange, {})
            all_assets = set(current.keys()) | set(initial.keys())
            exchange_pnl: dict[str, float] = {}
            for asset in all_assets:
                cur_val = current.get(asset, 0.0)
                init_val = initial.get(asset, 0.0)
                diff = cur_val - init_val
                if abs(diff) > 1e-12:
                    exchange_pnl[asset] = diff
            if exchange_pnl:
                pnl[exchange] = exchange_pnl

        return pnl

    def _get_balance(self, exchange: str, asset: str) -> float:
        """Get current balance for an asset on an exchange."""
        return self.balances.get(exchange, {}).get(asset, 0.0)

    def _adjust_balance(self, exchange: str, asset: str, delta: float) -> None:
        """Adjust balance for an asset on an exchange."""
        if exchange not in self.balances:
            self.balances[exchange] = {}
        current = self.balances[exchange].get(asset, 0.0)
        self.balances[exchange][asset] = current + delta
