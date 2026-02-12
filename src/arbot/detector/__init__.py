"""Arbitrage opportunity detection."""

from arbot.detector.cointegration import (
    CointegrationAnalyzer,
    CointegrationResult,
    JohansenResult,
)
from arbot.detector.pair_scanner import CointegratedPair, PairScanner
from arbot.detector.statistical import StatisticalDetector
from arbot.detector.zscore import ZScoreGenerator, ZScoreResult, ZScoreSignal

__all__ = [
    "CointegrationAnalyzer",
    "CointegrationResult",
    "CointegratedPair",
    "JohansenResult",
    "PairScanner",
    "StatisticalDetector",
    "ZScoreGenerator",
    "ZScoreResult",
    "ZScoreSignal",
]
