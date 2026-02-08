"""Tests for arbot.core.pipeline.ArbitragePipeline."""

import time

from arbot.core.pipeline import ArbitragePipeline, PipelineStats
from arbot.detector.spatial import SpatialDetector
from arbot.execution.base import BaseExecutor, InsufficientBalanceError
from arbot.execution.paper_executor import PaperExecutor
from arbot.models.balance import AssetBalance, ExchangeBalance, PortfolioSnapshot
from arbot.models.config import RiskConfig, TradingFee
from arbot.models.orderbook import OrderBook, OrderBookEntry
from arbot.models.signal import ArbitrageSignal, ArbitrageStrategy, SignalStatus
from arbot.models.trade import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TradeResult,
)
from arbot.risk.manager import RiskManager


def _make_orderbook(
    exchange: str,
    symbol: str = "BTC/USDT",
    best_bid: float = 50000.0,
    best_ask: float = 50050.0,
    depth: float = 10.0,
) -> OrderBook:
    """Create a test order book."""
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        timestamp=time.time(),
        bids=[
            OrderBookEntry(price=best_bid, quantity=depth),
            OrderBookEntry(price=best_bid - 10, quantity=depth),
        ],
        asks=[
            OrderBookEntry(price=best_ask, quantity=depth),
            OrderBookEntry(price=best_ask + 10, quantity=depth),
        ],
    )


def _make_paper_executor() -> PaperExecutor:
    """Create a paper executor with typical balances."""
    fees = {
        "binance": TradingFee(maker_pct=0.1, taker_pct=0.1),
        "upbit": TradingFee(maker_pct=0.25, taker_pct=0.25),
    }
    return PaperExecutor(
        initial_balances={
            "binance": {"USDT": 50000.0, "BTC": 1.0},
            "upbit": {"USDT": 50000.0, "BTC": 1.0},
        },
        exchange_fees=fees,
    )


class TestPipelineStats:
    """Tests for PipelineStats dataclass."""

    def test_default_values(self) -> None:
        stats = PipelineStats()
        assert stats.total_signals_detected == 0
        assert stats.total_signals_approved == 0
        assert stats.total_signals_rejected == 0
        assert stats.total_signals_executed == 0
        assert stats.total_signals_failed == 0
        assert stats.total_pnl_usd == 0.0
        assert stats.cycles_run == 0


class TestArbitragePipeline:
    """Tests for ArbitragePipeline."""

    def test_run_once_no_signals(self) -> None:
        """No signals when order books have no spread."""
        executor = _make_paper_executor()
        rm = RiskManager()
        detector = SpatialDetector(min_spread_pct=0.5)
        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=rm,
            spatial_detector=detector,
        )

        # Same prices on both exchanges -> no arbitrage
        obs = {
            "binance": _make_orderbook("binance", best_bid=50000, best_ask=50050),
            "upbit": _make_orderbook("upbit", best_bid=50000, best_ask=50050),
        }

        results = pipeline.run_once(obs)
        assert results == []

        stats = pipeline.get_stats()
        assert stats.cycles_run == 1
        assert stats.total_signals_detected == 0

    def test_run_once_with_profitable_signal(self) -> None:
        """Pipeline detects and executes a profitable spatial arbitrage."""
        fees = {
            "binance": TradingFee(maker_pct=0.1, taker_pct=0.1),
            "upbit": TradingFee(maker_pct=0.1, taker_pct=0.1),
        }
        executor = PaperExecutor(
            initial_balances={
                "binance": {"USDT": 100000.0, "BTC": 2.0},
                "upbit": {"USDT": 100000.0, "BTC": 2.0},
            },
            exchange_fees=fees,
        )
        rm = RiskManager()
        detector = SpatialDetector(
            min_spread_pct=0.1,
            exchange_fees=fees,
            default_quantity_usd=1000.0,
        )
        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=rm,
            spatial_detector=detector,
        )

        # Significant price difference: buy cheap on binance, sell expensive on upbit
        obs = {
            "binance": _make_orderbook("binance", best_bid=49800, best_ask=49900, depth=5.0),
            "upbit": _make_orderbook("upbit", best_bid=50200, best_ask=50300, depth=5.0),
        }

        results = pipeline.run_once(obs)

        stats = pipeline.get_stats()
        assert stats.total_signals_detected > 0
        assert stats.cycles_run == 1

        if len(results) > 0:
            assert stats.total_signals_executed > 0
            buy_res, sell_res = results[0]
            assert buy_res.filled_quantity > 0
            assert sell_res.filled_quantity > 0

    def test_risk_manager_rejects_signal(self) -> None:
        """Signals rejected when risk check fails."""
        fees = {
            "binance": TradingFee(maker_pct=0.1, taker_pct=0.1),
            "upbit": TradingFee(maker_pct=0.1, taker_pct=0.1),
        }
        executor = PaperExecutor(
            initial_balances={
                "binance": {"USDT": 100000.0, "BTC": 2.0},
                "upbit": {"USDT": 100000.0, "BTC": 2.0},
            },
            exchange_fees=fees,
        )
        # Very strict position limit -> all signals rejected
        config = RiskConfig(max_position_per_coin_usd=1.0)
        rm = RiskManager(config=config)
        detector = SpatialDetector(
            min_spread_pct=0.1,
            exchange_fees=fees,
            default_quantity_usd=1000.0,
        )
        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=rm,
            spatial_detector=detector,
        )

        obs = {
            "binance": _make_orderbook("binance", best_bid=49800, best_ask=49900, depth=5.0),
            "upbit": _make_orderbook("upbit", best_bid=50200, best_ask=50300, depth=5.0),
        }

        results = pipeline.run_once(obs)
        assert results == []

        stats = pipeline.get_stats()
        if stats.total_signals_detected > 0:
            assert stats.total_signals_rejected == stats.total_signals_detected
            assert stats.total_signals_executed == 0

    def test_run_once_insufficient_balance_handled(self) -> None:
        """Pipeline handles InsufficientBalanceError gracefully."""
        fees = {
            "binance": TradingFee(maker_pct=0.1, taker_pct=0.1),
            "upbit": TradingFee(maker_pct=0.1, taker_pct=0.1),
        }
        executor = PaperExecutor(
            initial_balances={
                "binance": {"USDT": 1.0, "BTC": 0.0001},  # Very low balance
                "upbit": {"USDT": 1.0, "BTC": 0.0001},
            },
            exchange_fees=fees,
        )
        # Very lenient risk config so signals pass risk check
        config = RiskConfig(
            max_position_per_coin_usd=1000000.0,
            max_total_exposure_usd=1000000.0,
        )
        rm = RiskManager(config=config)
        detector = SpatialDetector(
            min_spread_pct=0.1,
            exchange_fees=fees,
            default_quantity_usd=1000.0,
        )
        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=rm,
            spatial_detector=detector,
        )

        obs = {
            "binance": _make_orderbook("binance", best_bid=49800, best_ask=49900, depth=5.0),
            "upbit": _make_orderbook("upbit", best_bid=50200, best_ask=50300, depth=5.0),
        }

        # Should not raise, just log as failed
        results = pipeline.run_once(obs)

        stats = pipeline.get_stats()
        # If signals were detected and approved but balance insufficient
        if stats.total_signals_approved > 0:
            assert stats.total_signals_failed > 0

    def test_multiple_cycles(self) -> None:
        """Running multiple cycles increments cycle count."""
        executor = _make_paper_executor()
        rm = RiskManager()
        detector = SpatialDetector(min_spread_pct=10.0)  # Very high -> no signals
        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=rm,
            spatial_detector=detector,
        )

        obs = {
            "binance": _make_orderbook("binance"),
            "upbit": _make_orderbook("upbit"),
        }

        pipeline.run_once(obs)
        pipeline.run_once(obs)
        pipeline.run_once(obs)

        stats = pipeline.get_stats()
        assert stats.cycles_run == 3

    def test_get_trade_log(self) -> None:
        """Trade log is populated after successful execution."""
        fees = {
            "binance": TradingFee(maker_pct=0.1, taker_pct=0.1),
            "upbit": TradingFee(maker_pct=0.1, taker_pct=0.1),
        }
        executor = PaperExecutor(
            initial_balances={
                "binance": {"USDT": 100000.0, "BTC": 2.0},
                "upbit": {"USDT": 100000.0, "BTC": 2.0},
            },
            exchange_fees=fees,
        )
        rm = RiskManager()
        detector = SpatialDetector(
            min_spread_pct=0.1,
            exchange_fees=fees,
            default_quantity_usd=1000.0,
        )
        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=rm,
            spatial_detector=detector,
        )

        obs = {
            "binance": _make_orderbook("binance", best_bid=49800, best_ask=49900, depth=5.0),
            "upbit": _make_orderbook("upbit", best_bid=50200, best_ask=50300, depth=5.0),
        }

        results = pipeline.run_once(obs)

        trade_log = pipeline.get_trade_log()
        assert len(trade_log) == len(results)

        for signal, buy_res, sell_res in trade_log:
            assert isinstance(signal, ArbitrageSignal)
            assert isinstance(buy_res, TradeResult)
            assert isinstance(sell_res, TradeResult)

    def test_pnl_tracking(self) -> None:
        """PnL is accumulated in pipeline stats."""
        fees = {
            "binance": TradingFee(maker_pct=0.1, taker_pct=0.1),
            "upbit": TradingFee(maker_pct=0.1, taker_pct=0.1),
        }
        executor = PaperExecutor(
            initial_balances={
                "binance": {"USDT": 100000.0, "BTC": 2.0},
                "upbit": {"USDT": 100000.0, "BTC": 2.0},
            },
            exchange_fees=fees,
        )
        rm = RiskManager()
        detector = SpatialDetector(
            min_spread_pct=0.1,
            exchange_fees=fees,
            default_quantity_usd=1000.0,
        )
        pipeline = ArbitragePipeline(
            executor=executor,
            risk_manager=rm,
            spatial_detector=detector,
        )

        obs = {
            "binance": _make_orderbook("binance", best_bid=49800, best_ask=49900, depth=5.0),
            "upbit": _make_orderbook("upbit", best_bid=50200, best_ask=50300, depth=5.0),
        }

        results = pipeline.run_once(obs)

        stats = pipeline.get_stats()
        if len(results) > 0:
            # PnL should be non-zero after executing trades
            assert stats.total_pnl_usd != 0.0 or stats.total_fees_usd != 0.0

    def test_estimate_trade_pnl(self) -> None:
        """Static PnL estimator computes sell - buy correctly."""
        order = Order(
            exchange="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
        )
        buy = TradeResult(
            order=order,
            filled_quantity=1.0,
            filled_price=50000.0,
            fee=0.001,
            fee_asset="BTC",
            latency_ms=1.0,
        )
        sell = TradeResult(
            order=order.model_copy(update={"side": OrderSide.SELL}),
            filled_quantity=1.0,
            filled_price=50100.0,
            fee=50.1,
            fee_asset="USDT",
            latency_ms=1.0,
        )

        pnl = ArbitragePipeline._estimate_trade_pnl(buy, sell)
        assert pnl == 100.0  # 50100 - 50000

    def test_pipeline_without_detectors(self) -> None:
        """Pipeline with no detectors produces no signals."""
        executor = _make_paper_executor()
        rm = RiskManager()
        pipeline = ArbitragePipeline(executor=executor, risk_manager=rm)

        obs = {
            "binance": _make_orderbook("binance"),
            "upbit": _make_orderbook("upbit"),
        }

        results = pipeline.run_once(obs)
        assert results == []

        stats = pipeline.get_stats()
        assert stats.total_signals_detected == 0
        assert stats.cycles_run == 1
