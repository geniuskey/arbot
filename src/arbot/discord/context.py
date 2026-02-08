"""Bot context bundle for Discord command handlers."""

from __future__ import annotations

from dataclasses import dataclass

from arbot.config import AppConfig
from arbot.core.pipeline import ArbitragePipeline
from arbot.core.simulator import PaperTradingSimulator
from arbot.execution.paper_executor import PaperExecutor
from arbot.risk.manager import RiskManager


@dataclass
class BotContext:
    """References to core system components for Discord commands.

    Passed to all cogs so they can query pipeline state, executor portfolio,
    risk manager status, and control the simulator.

    Args:
        config: Application configuration.
        pipeline: Arbitrage pipeline for stats and trade log.
        simulator: Paper trading simulator for start/stop control.
        executor: Paper executor for portfolio and PnL.
        risk_manager: Risk manager for risk state.
    """

    config: AppConfig
    pipeline: ArbitragePipeline
    simulator: PaperTradingSimulator
    executor: PaperExecutor
    risk_manager: RiskManager
