"""Paper trading simulator that runs the arbitrage pipeline on live data.

Connects to exchange data sources and repeatedly runs the pipeline,
producing a simulation report with performance metrics.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from arbot.core.pipeline import ArbitragePipeline, PipelineStats


@dataclass
class SimulationReport:
    """Comprehensive simulation performance report.

    Attributes:
        started_at: Simulation start time.
        ended_at: Simulation end time (None if still running).
        duration_seconds: Total simulation duration.
        pipeline_stats: Aggregated pipeline statistics.
        final_pnl_usd: Net PnL at simulation end.
        total_fees_usd: Total fees paid during simulation.
        win_rate: Proportion of profitable trades (0 to 1).
        trade_count: Total number of executed trade pairs.
    """

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    duration_seconds: float = 0.0
    pipeline_stats: PipelineStats = field(default_factory=PipelineStats)
    final_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0


class PaperTradingSimulator:
    """Runs the arbitrage pipeline in a continuous loop on live price data.

    Uses exchange connectors to receive order book updates and feeds
    them into the pipeline for detection, risk checking, and simulated
    execution.

    Attributes:
        pipeline: The arbitrage pipeline to run.
        interval_seconds: Seconds between pipeline cycles.
    """

    def __init__(
        self,
        pipeline: ArbitragePipeline,
        interval_seconds: float = 1.0,
        on_trade: OnTradeCallback | None = None,
    ) -> None:
        """Initialize the simulator.

        Args:
            pipeline: Configured ArbitragePipeline instance.
            interval_seconds: Delay between pipeline cycles.
            on_trade: Async callback invoked for each executed trade pair.
        """
        self.pipeline = pipeline
        self.interval_seconds = interval_seconds
        self._running = False
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._task: asyncio.Task | None = None
        self._orderbook_provider: OrderBookProvider | None = None
        self._winning_trades: int = 0
        self._total_trades: int = 0
        self._on_trade = on_trade

    async def start(
        self,
        orderbook_provider: OrderBookProvider | None = None,
    ) -> None:
        """Start the simulation loop.

        Args:
            orderbook_provider: Callable that returns current order books.
                If None, uses a no-op provider (useful for testing with
                manual orderbook updates).
        """
        if self._running:
            return

        self._running = True
        self._started_at = datetime.now(UTC)
        self._orderbook_provider = orderbook_provider
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the simulation loop and wait for completion."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._stopped_at = datetime.now(UTC)

    async def _run_loop(self) -> None:
        """Internal simulation loop."""
        while self._running:
            try:
                orderbooks = {}
                if self._orderbook_provider is not None:
                    orderbooks = await self._orderbook_provider()

                if orderbooks:
                    results = self.pipeline.run_once(orderbooks)
                    for buy_result, sell_result in results:
                        self._total_trades += 1
                        pnl = (
                            sell_result.filled_quantity * sell_result.filled_price
                            - buy_result.filled_quantity * buy_result.filled_price
                        )
                        if pnl > 0:
                            self._winning_trades += 1
                        if self._on_trade is not None:
                            try:
                                await self._on_trade(buy_result, sell_result, pnl)
                            except Exception:
                                pass

                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    def get_report(self) -> SimulationReport:
        """Generate a comprehensive simulation report.

        Returns:
            SimulationReport with all performance metrics.
        """
        stats = self.pipeline.get_stats()
        now = self._stopped_at or datetime.now(UTC)
        started = self._started_at or now

        duration = (now - started).total_seconds()
        win_rate = (
            self._winning_trades / self._total_trades
            if self._total_trades > 0
            else 0.0
        )

        return SimulationReport(
            started_at=started,
            ended_at=self._stopped_at,
            duration_seconds=duration,
            pipeline_stats=stats,
            final_pnl_usd=stats.total_pnl_usd,
            total_fees_usd=stats.total_fees_usd,
            win_rate=win_rate,
            trade_count=self._total_trades,
        )

    @property
    def is_running(self) -> bool:
        """Whether the simulator is currently running."""
        return self._running


# Type aliases for callbacks
from typing import Awaitable, Callable

from arbot.models.orderbook import OrderBook  # noqa: E402
from arbot.models.trade import TradeResult as TradeResultModel  # noqa: E402

OrderBookProvider = Callable[[], Awaitable[dict[str, OrderBook]]]
OnTradeCallback = Callable[
    [TradeResultModel, TradeResultModel, float], Awaitable[None]
]
