"""Backtest data loader for generating and loading order book tick data."""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

from arbot.logging import get_logger
from arbot.models.orderbook import OrderBook, OrderBookEntry

logger = get_logger(__name__)


class BacktestDataLoader:
    """Loads or generates order book tick data for backtesting.

    Provides utilities to create synthetic data for testing and
    a skeleton for loading historical data from CSV files.
    """

    @staticmethod
    def generate_sample_data(
        exchanges: list[str],
        symbols: list[str],
        num_ticks: int = 100,
        base_price: float = 50000.0,
        spread_range: tuple[float, float] = (0.001, 0.005),
    ) -> list[dict[str, OrderBook]]:
        """Generate synthetic order book tick data for backtesting.

        Creates a time series of order book snapshots with random
        walk price movements and configurable spread ranges. Each
        tick contains one OrderBook per exchange.

        Args:
            exchanges: List of exchange names (e.g. ["binance", "upbit"]).
            symbols: List of trading pairs. Only the first symbol is used
                for generating data.
            num_ticks: Number of ticks to generate.
            base_price: Starting mid-price for the random walk.
            spread_range: Tuple of (min_spread_pct, max_spread_pct) as
                fractions (e.g. 0.001 = 0.1%).

        Returns:
            List of tick dictionaries, each mapping exchange name to
            an OrderBook snapshot.
        """
        symbol = symbols[0] if symbols else "BTC/USDT"
        tick_data: list[dict[str, OrderBook]] = []
        price = base_price

        for tick_idx in range(num_ticks):
            timestamp = 1700000000.0 + tick_idx * 1.0
            orderbooks: dict[str, OrderBook] = {}

            for exchange in exchanges:
                # Each exchange gets slightly different pricing
                exchange_offset = random.uniform(-0.001, 0.001) * price
                mid = price + exchange_offset

                spread_pct = random.uniform(spread_range[0], spread_range[1])
                half_spread = mid * spread_pct / 2

                best_ask = mid + half_spread
                best_bid = mid - half_spread

                # Generate 5 levels of depth
                asks: list[OrderBookEntry] = []
                bids: list[OrderBookEntry] = []
                for level in range(5):
                    ask_price = best_ask + level * mid * 0.0002
                    bid_price = best_bid - level * mid * 0.0002
                    qty = random.uniform(0.01, 0.5)

                    asks.append(OrderBookEntry(price=ask_price, quantity=qty))
                    bids.append(OrderBookEntry(price=bid_price, quantity=qty))

                orderbooks[exchange] = OrderBook(
                    exchange=exchange,
                    symbol=symbol,
                    timestamp=timestamp,
                    bids=bids,
                    asks=asks,
                )

            tick_data.append(orderbooks)

            # Random walk for next tick
            price *= 1 + random.gauss(0, 0.001)

        logger.info(
            "generated_sample_data",
            exchanges=exchanges,
            symbol=symbol,
            num_ticks=num_ticks,
            base_price=base_price,
        )
        return tick_data

    @staticmethod
    def load_from_csv(file_path: str | Path) -> list[dict[str, OrderBook]]:
        """Load order book tick data from a CSV file.

        Expected CSV columns: timestamp, exchange, symbol,
        bid_price, bid_qty, ask_price, ask_qty.

        Args:
            file_path: Path to the CSV file.

        Returns:
            List of tick dictionaries mapping exchange name to OrderBook.

        Raises:
            FileNotFoundError: If the CSV file does not exist.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        rows_by_timestamp: dict[float, dict[str, list[dict]]] = {}

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = float(row["timestamp"])
                exchange = row["exchange"]
                if ts not in rows_by_timestamp:
                    rows_by_timestamp[ts] = {}
                if exchange not in rows_by_timestamp[ts]:
                    rows_by_timestamp[ts][exchange] = []
                rows_by_timestamp[ts][exchange].append(row)

        tick_data: list[dict[str, OrderBook]] = []
        for ts in sorted(rows_by_timestamp.keys()):
            orderbooks: dict[str, OrderBook] = {}
            for exchange, rows in rows_by_timestamp[ts].items():
                bids: list[OrderBookEntry] = []
                asks: list[OrderBookEntry] = []
                symbol = rows[0]["symbol"]
                for row in rows:
                    bids.append(
                        OrderBookEntry(
                            price=float(row["bid_price"]),
                            quantity=float(row["bid_qty"]),
                        )
                    )
                    asks.append(
                        OrderBookEntry(
                            price=float(row["ask_price"]),
                            quantity=float(row["ask_qty"]),
                        )
                    )
                bids.sort(key=lambda e: e.price, reverse=True)
                asks.sort(key=lambda e: e.price)
                orderbooks[exchange] = OrderBook(
                    exchange=exchange,
                    symbol=symbol,
                    timestamp=ts,
                    bids=bids,
                    asks=asks,
                )
            tick_data.append(orderbooks)

        logger.info("loaded_csv_data", file_path=str(path), num_ticks=len(tick_data))
        return tick_data
