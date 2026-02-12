"""Statistical arbitrage detector integrating cointegration and Z-Score.

Detects statistical arbitrage opportunities by maintaining price histories,
scanning for cointegrated pairs, and generating trading signals when
Z-Scores breach configurable thresholds.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from arbot.detector.cointegration import CointegrationAnalyzer
from arbot.detector.pair_scanner import CointegratedPair, PairScanner
from arbot.detector.zscore import ZScoreGenerator, ZScoreSignal
from arbot.logging import get_logger
from arbot.models.config import TradingFee
from arbot.models.orderbook import OrderBook
from arbot.models.signal import ArbitrageSignal, ArbitrageStrategy, SignalStatus

logger = get_logger(__name__)


class StatisticalDetector:
    """Detects statistical arbitrage opportunities using cointegration + Z-Score.

    Maintains rolling price histories, periodically rescans for cointegrated
    pairs, and emits ArbitrageSignal entries when Z-Score thresholds are breached.

    Args:
        lookback_window: Number of price observations for Z-Score rolling window.
        z_entry_threshold: Z-Score magnitude to trigger entry signals.
        z_exit_threshold: Z-Score magnitude below which to trigger exit.
        rescan_interval_hours: Hours between cointegration pair rescans.
        significance_level: P-value threshold for cointegration tests.
        exchange_fees: Mapping of exchange name to TradingFee.
        default_quantity_usd: Default trade size in USD.
    """

    def __init__(
        self,
        lookback_window: int = 100,
        z_entry_threshold: float = 2.0,
        z_exit_threshold: float = 0.5,
        rescan_interval_hours: float = 24.0,
        significance_level: float = 0.05,
        exchange_fees: dict[str, TradingFee] | None = None,
        default_quantity_usd: float = 1000.0,
    ) -> None:
        self.lookback_window = lookback_window
        self.z_entry_threshold = z_entry_threshold
        self.z_exit_threshold = z_exit_threshold
        self.rescan_interval_hours = rescan_interval_hours
        self.significance_level = significance_level
        self.exchange_fees: dict[str, TradingFee] = exchange_fees or {}
        self.default_quantity_usd = default_quantity_usd

        self._zscore_gen = ZScoreGenerator(
            entry_threshold=z_entry_threshold,
            exit_threshold=z_exit_threshold,
        )
        self._scanner = PairScanner(
            significance_level=significance_level,
            min_half_life=1.0,
            max_half_life=self.lookback_window * 2.0,
        )

        # Price histories: key = "exchange:symbol", value = list of mid prices
        self._price_history: dict[str, list[float]] = defaultdict(list)
        self._cointegrated_pairs: list[CointegratedPair] = []
        self._last_scan_time: float = 0.0

    def update_prices(self, symbol: str, exchange: str, price: float) -> None:
        """Update price history for a symbol on an exchange.

        Args:
            symbol: Trading pair symbol (e.g. "BTC/USDT").
            exchange: Exchange identifier.
            price: Current mid price.
        """
        key = f"{exchange}:{symbol}"
        self._price_history[key].append(price)

    def detect(
        self,
        orderbooks: dict[str, OrderBook],
    ) -> list[ArbitrageSignal]:
        """Detect statistical arbitrage opportunities.

        1. Update price history from orderbooks.
        2. Rescan for cointegrated pairs if needed.
        3. Compute Z-Scores for known pairs.
        4. Generate signals for entry/exit opportunities.

        Args:
            orderbooks: Mapping of exchange name to OrderBook.

        Returns:
            List of ArbitrageSignal for detected opportunities.
        """
        # Update price histories from orderbooks
        for exchange, ob in orderbooks.items():
            mid = ob.mid_price
            if mid > 0:
                self.update_prices(ob.symbol, exchange, mid)

        # Rescan for cointegrated pairs if interval elapsed
        if self._should_rescan():
            self._rescan_pairs()

        # Generate signals from known cointegrated pairs
        signals: list[ArbitrageSignal] = []

        for pair in self._cointegrated_pairs:
            signal = self._evaluate_pair(pair, orderbooks)
            if signal is not None:
                signals.append(signal)

        signals.sort(key=lambda s: abs(s.net_spread_pct), reverse=True)
        return signals

    def _should_rescan(self) -> bool:
        """Check whether it is time to rescan for cointegrated pairs."""
        now = time.monotonic()
        elapsed_hours = (now - self._last_scan_time) / 3600.0
        return elapsed_hours >= self.rescan_interval_hours or self._last_scan_time == 0.0

    def _rescan_pairs(self) -> None:
        """Rescan all price history series for cointegrated pairs."""
        price_data: dict[str, np.ndarray] = {}
        for key, prices in self._price_history.items():
            if len(prices) >= self.lookback_window:
                price_data[key] = np.array(prices[-self.lookback_window * 2 :])

        if len(price_data) < 2:
            self._cointegrated_pairs = []
            self._last_scan_time = time.monotonic()
            return

        self._cointegrated_pairs = self._scanner.scan(
            price_data, p_threshold=self.significance_level
        )
        self._last_scan_time = time.monotonic()

        logger.info(
            "pair_scan_completed",
            pairs_found=len(self._cointegrated_pairs),
            series_count=len(price_data),
        )

    def _evaluate_pair(
        self,
        pair: CointegratedPair,
        orderbooks: dict[str, OrderBook],
    ) -> ArbitrageSignal | None:
        """Evaluate a cointegrated pair for Z-Score signals.

        Args:
            pair: The cointegrated pair to evaluate.
            orderbooks: Current orderbooks keyed by exchange.

        Returns:
            ArbitrageSignal if an entry signal is generated, else None.
        """
        prices_a = self._price_history.get(pair.symbol_a)
        prices_b = self._price_history.get(pair.symbol_b)

        if prices_a is None or prices_b is None:
            return None

        min_len = min(len(prices_a), len(prices_b))
        if min_len < self.lookback_window:
            return None

        arr_a = np.array(prices_a[-min_len:])
        arr_b = np.array(prices_b[-min_len:])

        result = self._zscore_gen.compute(
            arr_a, arr_b, pair.hedge_ratio, self.lookback_window
        )

        # Only generate signals for entry opportunities
        if result.signal not in (ZScoreSignal.ENTRY_LONG, ZScoreSignal.ENTRY_SHORT):
            return None

        # Parse exchange:symbol keys
        exchange_a, symbol_a = pair.symbol_a.split(":", 1) if ":" in pair.symbol_a else ("unknown", pair.symbol_a)
        exchange_b, symbol_b = pair.symbol_b.split(":", 1) if ":" in pair.symbol_b else ("unknown", pair.symbol_b)

        # Determine buy/sell direction
        if result.signal == ZScoreSignal.ENTRY_LONG:
            buy_exchange = exchange_a
            sell_exchange = exchange_b
            buy_price = float(arr_a[-1])
            sell_price = float(arr_b[-1]) * pair.hedge_ratio
        else:
            buy_exchange = exchange_b
            sell_exchange = exchange_a
            buy_price = float(arr_b[-1])
            sell_price = float(arr_a[-1]) / pair.hedge_ratio if pair.hedge_ratio != 0 else 0.0

        gross_spread_pct = abs(result.zscore) * float(result.std) / float(result.mean) * 100 if result.mean != 0 else 0.0

        # Apply fees
        buy_fee = self.exchange_fees.get(buy_exchange, TradingFee(maker_pct=0.1, taker_pct=0.1))
        sell_fee = self.exchange_fees.get(sell_exchange, TradingFee(maker_pct=0.1, taker_pct=0.1))
        total_fee_pct = buy_fee.taker_pct + sell_fee.taker_pct
        net_spread_pct = gross_spread_pct - total_fee_pct

        if net_spread_pct <= 0:
            return None

        quantity = self.default_quantity_usd / buy_price if buy_price > 0 else 0.0
        estimated_profit = self.default_quantity_usd * net_spread_pct / 100

        # Confidence based on Z-Score magnitude and p-value
        z_confidence = min(abs(result.zscore) / (self.z_entry_threshold * 2), 1.0)
        p_confidence = 1.0 - pair.p_value
        confidence = min((z_confidence + p_confidence) / 2, 1.0)

        # Determine orderbook depth from available orderbooks
        depth_usd = 0.0
        for ob in orderbooks.values():
            if ob.bids and ob.asks:
                depth_usd += sum(e.price * e.quantity for e in ob.bids[:3])

        symbol = symbol_a if symbol_a == symbol_b else f"{symbol_a}/{symbol_b}"

        return ArbitrageSignal(
            strategy=ArbitrageStrategy.STATISTICAL,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            symbol=symbol,
            buy_price=buy_price,
            sell_price=sell_price,
            quantity=quantity,
            gross_spread_pct=gross_spread_pct,
            net_spread_pct=net_spread_pct,
            estimated_profit_usd=estimated_profit,
            confidence=confidence,
            orderbook_depth_usd=depth_usd,
            status=SignalStatus.DETECTED,
            metadata={
                "zscore": result.zscore,
                "half_life": pair.half_life,
                "hedge_ratio": pair.hedge_ratio,
                "p_value": pair.p_value,
                "signal_type": result.signal.value,
            },
        )

    @property
    def known_pairs(self) -> list[CointegratedPair]:
        """Return currently known cointegrated pairs."""
        return list(self._cointegrated_pairs)
