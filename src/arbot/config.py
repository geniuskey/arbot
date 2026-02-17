"""Configuration management with Pydantic Settings and YAML loading."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(str, Enum):
    """Trading execution mode."""

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


# --- Sub-config models ---


class SystemConfig(BaseModel):
    """Top-level system settings."""

    execution_mode: ExecutionMode = ExecutionMode.PAPER
    log_level: str = "INFO"
    timezone: str = "UTC"


class SpatialDetectorConfig(BaseModel):
    """Spatial arbitrage detector settings."""

    enabled: bool = True
    min_spread_pct: float = 0.25
    min_depth_usd: float = 1000.0
    max_latency_ms: int = 500
    use_gross_spread: bool = False


class TriangularDetectorConfig(BaseModel):
    """Triangular arbitrage detector settings."""

    enabled: bool = True
    min_profit_pct: float = 0.15
    paths: list[list[str]] = Field(default_factory=list)


class StatisticalDetectorConfig(BaseModel):
    """Statistical arbitrage detector settings."""

    enabled: bool = False
    lookback_periods: int = 60
    entry_zscore: float = 2.0
    exit_zscore: float = 0.5
    p_value_threshold: float = 0.05


class DetectorConfig(BaseModel):
    """Arbitrage opportunity detector settings."""

    spatial: SpatialDetectorConfig = Field(default_factory=SpatialDetectorConfig)
    triangular: TriangularDetectorConfig = Field(default_factory=TriangularDetectorConfig)
    statistical: StatisticalDetectorConfig = Field(default_factory=StatisticalDetectorConfig)


class RiskConfig(BaseModel):
    """Risk management settings."""

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
    min_net_spread_pct: float = 0.0


class RebalancerConfig(BaseModel):
    """Rebalancer settings."""

    check_interval_minutes: int = 60
    imbalance_threshold_pct: float = 30.0
    min_transfer_usd: float = 100.0
    target_allocation: dict[str, float] = Field(default_factory=dict)
    preferred_networks: dict[str, list[str]] = Field(default_factory=dict)


class TelegramAlertConfig(BaseModel):
    """Telegram alert settings."""

    enabled: bool = True
    chat_id: str = ""
    bot_token: str = ""


class DiscordAlertConfig(BaseModel):
    """Discord bot settings."""

    enabled: bool = False
    bot_token: str = ""
    guild_id: int = 0
    channel_id: int = 0


class AlertThresholdsConfig(BaseModel):
    """Alert threshold settings."""

    opportunity_min_pct: float = 0.5
    daily_pnl_alert: bool = True
    error_alert: bool = True


class AlertsConfig(BaseModel):
    """Alerting settings."""

    telegram: TelegramAlertConfig = Field(default_factory=TelegramAlertConfig)
    discord: DiscordAlertConfig = Field(default_factory=DiscordAlertConfig)
    thresholds: AlertThresholdsConfig = Field(default_factory=AlertThresholdsConfig)


class PostgresConfig(BaseModel):
    """PostgreSQL database settings."""

    host: str = "localhost"
    port: int = 5432
    database: str = "arbot"
    user: str = "arbot"
    password: str = ""

    @property
    def dsn(self) -> str:
        """Build PostgreSQL DSN string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


class ClickHouseConfig(BaseModel):
    """ClickHouse database settings."""

    host: str = "localhost"
    port: int = 9000
    database: str = "arbot"


class RedisConfig(BaseModel):
    """Redis settings."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""

    @property
    def url(self) -> str:
        """Build Redis URL string."""
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class DatabaseConfig(BaseModel):
    """Database settings."""

    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    clickhouse: ClickHouseConfig = Field(default_factory=ClickHouseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)


class RateLimitConfig(BaseModel):
    """Exchange rate limit settings."""

    type: str = "count"
    limit: int = 100
    window: int = 60
    default_limit: int | None = None


class WebSocketConfig(BaseModel):
    """Exchange WebSocket settings."""

    orderbook_depth: int = 10
    reconnect_delay_s: int = 5
    max_reconnect_attempts: int = 10


class ExchangeConfig(BaseModel):
    """Per-exchange configuration."""

    tier: int = 2
    maker_fee_pct: float = 0.10
    taker_fee_pct: float = 0.10
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)


# --- Main config ---


class AppConfig(BaseSettings):
    """Application configuration.

    Loads from YAML file, with environment variable overrides.
    """

    model_config = SettingsConfigDict(
        env_prefix="ARBOT_",
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        """Environment variables override YAML (init) values."""
        return env_settings, init_settings, dotenv_settings, file_secret_settings

    system: SystemConfig = Field(default_factory=SystemConfig)
    exchanges_enabled: list[str] = Field(
        default_factory=lambda: ["binance", "okx", "bybit", "upbit", "kucoin"],
        alias="exchanges_enabled",
    )
    symbols: list[str] = Field(
        default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"]
    )
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    rebalancer: RebalancerConfig = Field(default_factory=RebalancerConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    exchange_configs: dict[str, ExchangeConfig] = Field(default_factory=dict)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries. Override values take precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(
    config_dir: str | Path = "configs",
    config_file: str = "default.yaml",
    exchanges_file: str = "exchanges.yaml",
) -> AppConfig:
    """Load application configuration from YAML files with env var overrides.

    Args:
        config_dir: Path to the configuration directory.
        config_file: Name of the main config YAML file.
        exchanges_file: Name of the exchanges config YAML file.

    Returns:
        Validated AppConfig instance.
    """
    config_path = Path(config_dir)

    # Load main config
    main_config_path = config_path / config_file
    raw: dict[str, Any] = {}
    if main_config_path.exists():
        raw = _load_yaml(main_config_path)

    # Extract exchanges.enabled into flat key
    if "exchanges" in raw and isinstance(raw["exchanges"], dict):
        exchanges_data = raw.pop("exchanges")
        if "enabled" in exchanges_data:
            raw["exchanges_enabled"] = exchanges_data["enabled"]

    # Load exchange configs
    exchanges_config_path = config_path / exchanges_file
    exchange_configs: dict[str, ExchangeConfig] = {}
    if exchanges_config_path.exists():
        exchanges_raw = _load_yaml(exchanges_config_path)
        if "exchanges" in exchanges_raw:
            for name, cfg in exchanges_raw["exchanges"].items():
                exchange_configs[name] = ExchangeConfig(**cfg)

    raw["exchange_configs"] = exchange_configs

    # Pydantic Settings will automatically apply env var overrides
    return AppConfig(**raw)
