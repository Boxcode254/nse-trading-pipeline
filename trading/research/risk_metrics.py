"""Expanded risk and performance metrics for the research platform.

Extends the basic ``BacktestResult`` with:

- Sortino Ratio (downside deviation instead of total std)
- Annualised volatility
- Calmar Ratio (return / max drawdown)
- Time in market (%)
- Exposure
- Trade frequency (trades per year)
- Average holding period (calendar days)

Each metric is computed as a standalone function so they can be
used independently.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════════
# Standalone metric functions
# ═══════════════════════════════════════════════════════════════════


def annualised_volatility(daily_returns: np.ndarray) -> float:
    """Annualised standard deviation of daily returns."""
    if len(daily_returns) < 2 or daily_returns.std() == 0:
        return 0.0
    return float(daily_returns.std() * np.sqrt(252))


def sortino_ratio(
    daily_returns: np.ndarray,
    risk_free_rate: float = 0.0,
) -> float:
    """Sortino Ratio — like Sharpe but uses downside deviation only.

    Downside deviation considers only returns below zero (or below
    the risk-free rate).
    """
    if len(daily_returns) < 2:
        return 0.0
    excess = daily_returns - risk_free_rate / 252
    downside = daily_returns[daily_returns < 0]
    if len(downside) < 2 or downside.std() == 0:
        return 0.0
    downside_dev = downside.std() * np.sqrt(252)
    return float(np.mean(excess) * 252 / downside_dev) if downside_dev != 0 else 0.0


def calmar_ratio(annualised_return: float, max_drawdown_pct: float) -> float:
    """Return / Max Drawdown — measures return per unit of peak-to-trough risk."""
    if max_drawdown_pct == 0:
        return 0.0
    return round(annualised_return / max_drawdown_pct, 2)


def time_in_market(trades: list) -> float:
    """Percentage of trading days the strategy was in a position.

    Parameters
    ----------
    trades : list[Trade]
        Completed trades with ``entry_date`` and ``exit_date`` (pd.Timestamp).

    Returns
    -------
    float
        Percentage (0–100%) of calendar days spent in the market.
    """
    if not trades:
        return 0.0
    total_days = sum((t.exit_date - t.entry_date).days for t in trades)
    if not trades[-1].exit_date or not trades[0].entry_date:
        return 0.0
    full_period = (trades[-1].exit_date - trades[0].entry_date).days
    if full_period <= 0:
        return 0.0
    return round(total_days / full_period * 100, 1)


def trade_frequency(trades: list, trading_days: int) -> float:
    """Number of trades per year (252 trading days)."""
    if trading_days <= 0 or not trades:
        return 0.0
    years = trading_days / 252
    return round(len(trades) / years, 1) if years > 0 else 0.0


def avg_holding_calendar_days(trades: list) -> float:
    """Average holding period in calendar days."""
    if not trades:
        return 0.0
    days = [(t.exit_date - t.entry_date).days for t in trades]
    return round(float(np.mean(days)), 1)


# ═══════════════════════════════════════════════════════════════════
# Composite: compute all metrics from an equity curve + trades
# ═══════════════════════════════════════════════════════════════════


def compute_expanded_metrics(
    equity_curve: list[float],
    trades: list,
    close_series: pd.Series,
    warmup: int = 0,
) -> dict:
    """Compute the full set of risk/return/trading metrics.

    Returns a dict with keys suitable for JSON serialisation.
    """
    metrics: dict = {}

    eq = np.array(equity_curve) if equity_curve else np.array([1.0])
    n = len(eq)

    # ── Return metrics ────────────────────────────────────────────
    total_ret = (eq[-1] - 1.0) * 100
    metrics["total_return_pct"] = round(float(total_ret), 2)

    trading_days = n - warmup - 1  # bars after warmup, minus first (no returns)
    if trading_days > 0:
        years = max(trading_days / 252, 0.01)
        ann_ret = ((1 + total_ret / 100) ** (1 / years) - 1) * 100
        metrics["annualised_return_pct"] = round(float(ann_ret), 2)
    else:
        metrics["annualised_return_pct"] = 0.0

    # ── Risk metrics ──────────────────────────────────────────────
    if n > 2:
        daily_rets = np.diff(eq) / eq[:-1]
        metrics["volatility_pct"] = round(
            annualised_volatility(daily_rets) * 100, 2
        )
        metrics["sharpe_ratio"] = round(
            float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252))
            if daily_rets.std() > 0 else 0.0, 2
        )
        metrics["sortino_ratio"] = round(sortino_ratio(daily_rets), 2)
    else:
        metrics["volatility_pct"] = 0.0
        metrics["sharpe_ratio"] = 0.0
        metrics["sortino_ratio"] = 0.0

    # Max drawdown
    if n > 1:
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        metrics["max_drawdown_pct"] = round(abs(float(np.min(dd))) * 100, 2)
    else:
        metrics["max_drawdown_pct"] = 0.0

    # Calmar
    if metrics.get("annualised_return_pct", 0) != 0 and metrics.get("max_drawdown_pct", 0) > 0:
        metrics["calmar_ratio"] = round(
            metrics["annualised_return_pct"] / metrics["max_drawdown_pct"], 2
        )
    else:
        metrics["calmar_ratio"] = 0.0

    # ── Trading metrics ───────────────────────────────────────────
    if trades:
        returns = [t.return_pct for t in trades]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]

        metrics["total_trades"] = len(trades)
        metrics["winning_trades"] = len(wins)
        metrics["losing_trades"] = len(losses)
        metrics["win_rate_pct"] = round(len(wins) / len(trades) * 100, 1)
        metrics["avg_return_pct"] = round(float(np.mean(returns)), 2)
        metrics["avg_win_pct"] = round(float(np.mean(wins)), 2) if wins else 0.0
        metrics["avg_loss_pct"] = round(float(np.mean(losses)), 2) if losses else 0.0

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        metrics["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

        metrics["avg_bars_held"] = round(float(np.mean([t.bars_held for t in trades])), 1)
        metrics["avg_holding_days"] = avg_holding_calendar_days(trades)
        metrics["trade_frequency_per_year"] = trade_frequency(trades, trading_days)
        metrics["time_in_market_pct"] = time_in_market(trades)
    else:
        for k in ("total_trades", "winning_trades", "losing_trades", "win_rate_pct",
                  "avg_return_pct", "avg_win_pct", "avg_loss_pct", "profit_factor",
                  "avg_bars_held", "avg_holding_days", "trade_frequency_per_year",
                  "time_in_market_pct"):
            metrics[k] = 0.0 if "pct" in k or "ratio" in k else 0

    return metrics
