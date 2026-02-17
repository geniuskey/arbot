"""Tests for ArBot main entrypoint."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arbot.config import AppConfig, ExchangeConfig, ExecutionMode
from arbot.main import (
    _build_exchange_fees,
    _build_exchange_info,
    _build_initial_balances,
    _create_connectors,
    parse_args,
)
from arbot.models.config import ExchangeInfo, TradingFee


# --- parse_args tests ---


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_default_args(self) -> None:
        args = parse_args([])
        assert args.config_dir == "configs"
        assert args.mode is None

    def test_config_dir(self) -> None:
        args = parse_args(["--config-dir", "/tmp/myconfigs"])
        assert args.config_dir == "/tmp/myconfigs"

    def test_mode_paper(self) -> None:
        args = parse_args(["--mode", "paper"])
        assert args.mode == "paper"

    def test_mode_backtest(self) -> None:
        args = parse_args(["--mode", "backtest"])
        assert args.mode == "backtest"

    def test_invalid_mode(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--mode", "live"])

    def test_combined_args(self) -> None:
        args = parse_args(["--config-dir", "alt_configs", "--mode", "paper"])
        assert args.config_dir == "alt_configs"
        assert args.mode == "paper"


# --- _build_exchange_info tests ---


class TestBuildExchangeInfo:
    """Tests for building ExchangeInfo from config."""

    def test_basic_info(self) -> None:
        ex_config = ExchangeConfig(
            tier=1,
            maker_fee_pct=0.08,
            taker_fee_pct=0.10,
        )
        info = _build_exchange_info("binance", ex_config)
        assert info.name == "binance"
        assert info.tier == 1
        assert info.is_active is True
        assert info.fees.maker_pct == 0.08
        assert info.fees.taker_pct == 0.10

    def test_default_config(self) -> None:
        info = _build_exchange_info("test_ex", ExchangeConfig())
        assert info.name == "test_ex"
        assert info.tier == 2
        assert info.fees.maker_pct == 0.10
        assert info.fees.taker_pct == 0.10


# --- _build_exchange_fees tests ---


class TestBuildExchangeFees:
    """Tests for building fee schedule from config."""

    def test_fees_for_all_enabled(self) -> None:
        config = AppConfig(
            exchanges_enabled=["binance", "upbit"],
            exchange_configs={
                "binance": ExchangeConfig(maker_fee_pct=0.08, taker_fee_pct=0.10),
                "upbit": ExchangeConfig(maker_fee_pct=0.25, taker_fee_pct=0.25),
            },
        )
        fees = _build_exchange_fees(config)
        assert "binance" in fees
        assert "upbit" in fees
        assert fees["binance"].maker_pct == 0.08
        assert fees["upbit"].taker_pct == 0.25

    def test_missing_exchange_config_uses_defaults(self) -> None:
        config = AppConfig(
            exchanges_enabled=["unknown_ex"],
            exchange_configs={},
        )
        fees = _build_exchange_fees(config)
        assert "unknown_ex" in fees
        assert fees["unknown_ex"].maker_pct == 0.10


# --- _build_initial_balances tests ---


class TestBuildInitialBalances:
    """Tests for building paper trading initial balances."""

    def test_balances_per_exchange(self) -> None:
        config = AppConfig(exchanges_enabled=["binance", "upbit"])
        balances = _build_initial_balances(config)
        assert "binance" in balances
        assert "upbit" in balances
        assert balances["binance"]["USDT"] == 1_000.0
        assert balances["binance"]["BTC"] == 0.01
        assert balances["upbit"]["USDT"] == 1_000.0
        assert balances["upbit"]["BTC"] == 0.01

    def test_empty_exchanges(self) -> None:
        config = AppConfig(exchanges_enabled=[])
        balances = _build_initial_balances(config)
        assert balances == {}


# --- _create_connectors tests ---


class TestCreateConnectors:
    """Tests for exchange connector creation."""

    def test_creates_binance_connector(self) -> None:
        config = AppConfig(
            exchanges_enabled=["binance"],
            exchange_configs={
                "binance": ExchangeConfig(tier=1),
            },
        )
        connectors = _create_connectors(config)
        assert len(connectors) == 1
        assert connectors[0].exchange_name == "binance"

    def test_creates_upbit_connector(self) -> None:
        config = AppConfig(
            exchanges_enabled=["upbit"],
            exchange_configs={
                "upbit": ExchangeConfig(tier=2),
            },
        )
        connectors = _create_connectors(config)
        assert len(connectors) == 1
        assert connectors[0].exchange_name == "upbit"

    def test_skips_unregistered_exchange(self) -> None:
        config = AppConfig(
            exchanges_enabled=["okx", "binance", "nonexistent"],
            exchange_configs={},
        )
        connectors = _create_connectors(config)
        # okx and binance have connector classes, nonexistent does not
        assert len(connectors) == 2
        exchange_names = {c.exchange_name for c in connectors}
        assert "nonexistent" not in exchange_names

    def test_empty_enabled_list(self) -> None:
        config = AppConfig(exchanges_enabled=[])
        connectors = _create_connectors(config)
        assert connectors == []

    def test_reads_api_keys_from_env(self) -> None:
        config = AppConfig(
            exchanges_enabled=["binance"],
            exchange_configs={"binance": ExchangeConfig()},
        )
        with patch.dict(
            "os.environ",
            {
                "ARBOT_BINANCE_API_KEY": "test_key",
                "ARBOT_BINANCE_API_SECRET": "test_secret",
            },
        ):
            connectors = _create_connectors(config)
            assert len(connectors) == 1
            # Verify the connector was created (we can't easily check
            # private attrs but at least it was constructed successfully)
            assert connectors[0].exchange_name == "binance"

    def test_multiple_connectors(self) -> None:
        config = AppConfig(
            exchanges_enabled=["binance", "upbit"],
            exchange_configs={
                "binance": ExchangeConfig(tier=1),
                "upbit": ExchangeConfig(tier=2),
            },
        )
        connectors = _create_connectors(config)
        assert len(connectors) == 2
        names = {c.exchange_name for c in connectors}
        assert names == {"binance", "upbit"}


# --- run function tests ---


class TestRun:
    """Tests for the main run function."""

    @pytest.mark.asyncio
    async def test_run_no_connectors_exits(self) -> None:
        """Run should exit gracefully when no connectors are available."""
        config = AppConfig(
            exchanges_enabled=["nonexistent_exchange"],
            exchange_configs={},
        )
        from arbot.main import run

        # Should return without error when no connectors are available
        await run(config)

    @pytest.mark.asyncio
    async def test_run_shutdown_signal(self) -> None:
        """Run should stop when shutdown event is set."""
        config = AppConfig(
            exchanges_enabled=["binance"],
            exchange_configs={"binance": ExchangeConfig()},
        )

        from arbot.main import run

        # Mock the major components to avoid real connections
        with (
            patch("arbot.main.RedisCache") as mock_redis_cls,
            patch("arbot.main.PriceCollector") as mock_collector_cls,
            patch("arbot.main.PaperTradingSimulator") as mock_sim_cls,
        ):
            mock_redis = AsyncMock()
            mock_redis_cls.return_value = mock_redis

            mock_collector = AsyncMock()
            mock_collector_cls.return_value = mock_collector

            mock_sim = MagicMock()
            mock_sim.start = AsyncMock()
            mock_sim.stop = AsyncMock()
            mock_sim.get_report.return_value = MagicMock(
                duration_seconds=0.0,
                pipeline_stats=MagicMock(
                    cycles_run=0,
                    total_signals_detected=0,
                    total_signals_executed=0,
                ),
                final_pnl_usd=0.0,
                total_fees_usd=0.0,
                win_rate=0.0,
                trade_count=0,
            )
            mock_sim_cls.return_value = mock_sim

            # Use an explicit shutdown event instead of os.kill(SIGINT)
            # which is unreliable on Windows asyncio event loops.
            shutdown_event = asyncio.Event()

            async def trigger_shutdown() -> None:
                await asyncio.sleep(0.1)
                shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await run(config, shutdown_event=shutdown_event)
            await task

            # Verify cleanup was called
            mock_sim.stop.assert_awaited_once()
            mock_collector.stop.assert_awaited_once()
            mock_redis.disconnect.assert_awaited_once()


# --- main function tests ---


class TestMain:
    """Tests for the main() entry point function."""

    def test_main_loads_config_and_runs(self) -> None:
        with (
            patch("arbot.main.load_config") as mock_load,
            patch("arbot.main.setup_logging") as mock_logging,
            patch("arbot.main.asyncio.run") as mock_run,
        ):
            mock_config = AppConfig(exchanges_enabled=[])
            mock_load.return_value = mock_config

            from arbot.main import main

            main(["--config-dir", "test_configs"])

            mock_load.assert_called_once_with(config_dir="test_configs")
            mock_logging.assert_called_once()
            mock_run.assert_called_once()

    def test_main_mode_override(self) -> None:
        with (
            patch("arbot.main.load_config") as mock_load,
            patch("arbot.main.setup_logging"),
            patch("arbot.main.asyncio.run"),
        ):
            mock_config = AppConfig(exchanges_enabled=[])
            mock_load.return_value = mock_config

            from arbot.main import main

            main(["--mode", "backtest"])

            assert mock_config.system.execution_mode == ExecutionMode.BACKTEST
