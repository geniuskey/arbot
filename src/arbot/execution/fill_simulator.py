"""Order fill simulator using order book depth.

Simulates realistic market order fills by walking the order book
level by level, computing volume-weighted average fill price and
applying trading fees.
"""

import time
import uuid
from datetime import UTC, datetime

from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook
from arbot.models.trade import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TradeResult,
)


class FillSimulator:
    """Simulates order fills against an order book.

    Walks the order book depth level by level to compute a realistic
    volume-weighted average price, then applies trading fees.
    """

    @staticmethod
    def simulate_fill(
        orderbook: OrderBook,
        side: OrderSide,
        quantity: float,
        fee: TradingFee,
    ) -> TradeResult:
        """Simulate filling a market order against an order book.

        For BUY orders, consumes the ask side (ascending prices).
        For SELL orders, consumes the bid side (descending prices).

        Args:
            orderbook: The order book to fill against.
            side: BUY or SELL.
            quantity: Quantity of base asset to fill.
            fee: Trading fee schedule (taker fee is used).

        Returns:
            TradeResult with fill details including VWAP and fees.
        """
        start_time = time.monotonic()

        entries = orderbook.asks if side == OrderSide.BUY else orderbook.bids
        remaining_qty = quantity
        total_cost = 0.0
        filled_qty = 0.0

        for entry in entries:
            if remaining_qty <= 0:
                break

            fill_at_level = min(remaining_qty, entry.quantity)
            total_cost += fill_at_level * entry.price
            filled_qty += fill_at_level
            remaining_qty -= fill_at_level

        vwap = total_cost / filled_qty if filled_qty > 0 else 0.0

        # Determine fill status
        if filled_qty <= 0:
            status = OrderStatus.FAILED
        elif remaining_qty > 1e-12:
            status = OrderStatus.PARTIAL
        else:
            status = OrderStatus.FILLED

        # Fee is charged on the received asset
        # BUY: fee on base asset (received), SELL: fee on quote asset (received)
        fee_pct = fee.taker_pct / 100
        if side == OrderSide.BUY:
            fee_amount = filled_qty * fee_pct
            fee_asset = orderbook.symbol.split("/")[0]  # base asset
        else:
            fee_amount = total_cost * fee_pct
            fee_asset = orderbook.symbol.split("/")[1]  # quote asset

        elapsed_ms = (time.monotonic() - start_time) * 1000

        order = Order(
            id=str(uuid.uuid4()),
            exchange=orderbook.exchange,
            symbol=orderbook.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=vwap if vwap > 0 else None,
            status=status,
        )

        return TradeResult(
            order=order,
            filled_quantity=filled_qty,
            filled_price=vwap,
            fee=fee_amount,
            fee_asset=fee_asset,
            latency_ms=elapsed_ms,
            filled_at=datetime.now(UTC),
        )
