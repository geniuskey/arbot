"""Backtest performance metrics calculation."""

from __future__ import annotations

import math

from pydantic import BaseModel


class BacktestResult(BaseModel):
    """Aggregated results from a backtest run.

    Attributes:
        total_pnl: Total profit and loss in USD.
        total_trades: Total number of trades executed.
        win_count: Number of profitable trades.
        loss_count: Number of losing trades.
        win_rate: Ratio of winning trades (0 to 1).
        sharpe_ratio: Annualized Sharpe ratio.
        max_drawdown_pct: Maximum peak-to-trough drawdown as percentage.
        profit_factor: Ratio of gross profits to gross losses.
        avg_profit_per_trade: Average PnL per trade.
        pnl_curve: Cumulative PnL time series.
    """

    total_pnl: float
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    sharpe_ratio: float
    max_drawdown_pct: float
    profit_factor: float
    avg_profit_per_trade: float
    pnl_curve: list[float]


class BacktestMetrics:
    """Calculates backtest performance metrics from trade PnL data."""

    @staticmethod
    def calculate(
        trade_pnls: list[float],
        initial_capital: float = 100_000.0,
    ) -> BacktestResult:
        """Calculate comprehensive backtest metrics.

        Args:
            trade_pnls: List of per-trade PnL values in USD.
            initial_capital: Starting capital in USD for return calculations.

        Returns:
            BacktestResult with all computed metrics.
        """
        total_trades = len(trade_pnls)

        if total_trades == 0:
            return BacktestResult(
                total_pnl=0.0,
                total_trades=0,
                win_count=0,
                loss_count=0,
                win_rate=0.0,
                sharpe_ratio=0.0,
                max_drawdown_pct=0.0,
                profit_factor=0.0,
                avg_profit_per_trade=0.0,
                pnl_curve=[],
            )

        total_pnl = sum(trade_pnls)
        win_count = sum(1 for p in trade_pnls if p > 0)
        loss_count = sum(1 for p in trade_pnls if p < 0)
        win_rate = win_count / total_trades
        avg_profit_per_trade = total_pnl / total_trades

        # Cumulative PnL curve
        pnl_curve: list[float] = []
        cumulative = 0.0
        for pnl in trade_pnls:
            cumulative += pnl
            pnl_curve.append(cumulative)

        # Sharpe ratio: annualized, based on per-trade returns
        returns = [p / initial_capital for p in trade_pnls]
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_return = math.sqrt(variance)
        if std_return > 0:
            sharpe_ratio = (mean_return / std_return) * math.sqrt(252)
        else:
            sharpe_ratio = 0.0

        # Max drawdown percentage (from cumulative PnL curve relative to capital)
        max_drawdown_pct = BacktestMetrics._calculate_max_drawdown(
            pnl_curve, initial_capital
        )

        # Profit factor: sum of wins / abs(sum of losses)
        gross_profit = sum(p for p in trade_pnls if p > 0)
        gross_loss = sum(p for p in trade_pnls if p < 0)
        if gross_loss < 0:
            profit_factor = gross_profit / abs(gross_loss)
        else:
            profit_factor = float("inf") if gross_profit > 0 else 0.0

        return BacktestResult(
            total_pnl=total_pnl,
            total_trades=total_trades,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=win_rate,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            profit_factor=profit_factor,
            avg_profit_per_trade=avg_profit_per_trade,
            pnl_curve=pnl_curve,
        )

    @staticmethod
    def _calculate_max_drawdown(
        pnl_curve: list[float], initial_capital: float
    ) -> float:
        """Calculate maximum drawdown percentage from a PnL curve.

        Args:
            pnl_curve: Cumulative PnL time series.
            initial_capital: Starting capital for percentage calculation.

        Returns:
            Maximum drawdown as a percentage (0 to 100).
        """
        if not pnl_curve:
            return 0.0

        # Equity curve = initial_capital + cumulative PnL
        peak = initial_capital + pnl_curve[0]
        max_dd = 0.0

        for cumulative_pnl in pnl_curve:
            equity = initial_capital + cumulative_pnl
            if equity > peak:
                peak = equity
            drawdown = (peak - equity) / peak * 100 if peak > 0 else 0.0
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd
