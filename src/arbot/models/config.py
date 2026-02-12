"""Configuration data models for exchanges and risk management."""

from typing import Any

from pydantic import BaseModel


class TradingFee(BaseModel):
    """Trading fee structure for an exchange.

    Attributes:
        maker_pct: Maker fee percentage.
        taker_pct: Taker fee percentage.
    """

    model_config = {"frozen": True}

    maker_pct: float
    taker_pct: float


class ExchangeInfo(BaseModel):
    """Exchange configuration and metadata.

    Attributes:
        name: Exchange identifier (e.g. "binance").
        tier: Exchange tier (1=top, 2=mid, 3=small).
        is_active: Whether this exchange is enabled.
        fees: Trading fee structure.
        rate_limit: Rate limiting configuration.
    """

    name: str
    tier: int
    is_active: bool = True
    fees: TradingFee
    rate_limit: dict[str, Any] = {}


class RiskConfig(BaseModel):
    """Risk management configuration parameters.

    Based on TRD.md Section 3.4 risk parameters.

    Attributes:
        max_position_per_coin_usd: Maximum position size per coin in USD.
        max_position_per_exchange_usd: Maximum position size per exchange in USD.
        max_total_exposure_usd: Maximum total portfolio exposure in USD.
        max_daily_loss_usd: Maximum allowed daily loss in USD.
        max_daily_loss_pct: Maximum allowed daily loss percentage.
        max_drawdown_pct: Maximum allowed drawdown percentage.
        price_deviation_threshold_pct: Threshold for anomalous price detection.
        max_spread_pct: Maximum allowed spread percentage.
        consecutive_loss_limit: Number of consecutive losses before circuit breaker.
        cooldown_minutes: Cooldown period after circuit breaker activation.
        flash_crash_pct: Percentage drop threshold for flash crash detection.
        spread_std_threshold: Standard deviations from mean spread for anomaly.
        stale_threshold_seconds: Maximum age of order book data in seconds.
        warning_threshold_pct: Percentage of limits before WARNING state.
    """

    max_position_per_coin_usd: float = 10_000
    max_position_per_exchange_usd: float = 50_000
    max_total_exposure_usd: float = 100_000
    max_daily_loss_usd: float = 500
    max_daily_loss_pct: float = 1.0
    max_drawdown_pct: float = 5.0
    price_deviation_threshold_pct: float = 10.0
    max_spread_pct: float = 5.0
    consecutive_loss_limit: int = 10
    cooldown_minutes: int = 30
    flash_crash_pct: float = 10.0
    spread_std_threshold: float = 3.0
    stale_threshold_seconds: float = 30.0
    warning_threshold_pct: float = 70.0
