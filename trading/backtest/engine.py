"""Core backtesting engine — strategy-agnostic, pluggable via BaseStrategy.

The engine accepts any strategy object, calls prepare() then
generate_signals(), and walks the resulting BUY/SELL/HOLD series
to produce trades and an equity curve.

Benchmark strategy is SmaCrossover() — frozen, never modified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from .. import config
from ..strategies.base import BaseStrategy
from ..strategies.sma_crossover import SmaCrossover


@dataclass
class Trade:
    """A single completed trade (entry → exit)."""
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    direction: str = "long"
    bars_held: int = 0
    return_pct: float = 0.0
    exit_reason: str = "signal"
    regime_at_entry: str = "unknown"


@dataclass
class BacktestResult:
    """Aggregated backtest result for one pair + one strategy."""
    pair: str
    strategy_name: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_return_pct: float = 0.0
    annualised_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0
    volatility_pct: float = 0.0
    calmar_ratio: float = 0.0
    avg_return_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    win_rate_pct: float = 0.0
    avg_bars_held: float = 0.0
    avg_holding_days: float = 0.0
    trade_frequency_per_year: float = 0.0
    time_in_market_pct: float = 0.0
    buy_and_hold_return_pct: float = 0.0
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    benchmark_curve: list[float] = field(default_factory=list)
    daily_signals: Optional[pd.Series] = None  # BUY/SELL/HOLD per bar
    regime_breakdown: Optional[dict] = field(default=None)  # regime analysis results
    data_start: Optional[str] = None
    data_end: Optional[str] = None


def run_backtest(
    pair: str,
    df: pd.DataFrame,
    strategy: Optional[BaseStrategy] = None,
    **kwargs: Any,
) -> BacktestResult:
    """Run a backtest over *df* for *pair* using *strategy*.

    Parameters
    ----------
    pair : str
        Human-readable pair name.
    df : pd.DataFrame
        OHLCV with columns ``open, high, low, close, volume``
        and a ``date`` DatetimeIndex.
    strategy : BaseStrategy, optional
        Strategy to test.  Defaults to ``SmaCrossover()`` (the
        frozen benchmark, Strategy A).
    **kwargs
        Ignored (legacy compatibility).

    Returns
    -------
    BacktestResult
        Trades, equity curve, and all metrics.
    """
    using_default = strategy is None
    strategy = strategy or SmaCrossover()

    result = BacktestResult(pair=pair, strategy_name=strategy.name)

    if df.empty or len(df) < 100:
        return result

    df = strategy.prepare(df.copy())
    signals = strategy.generate_signals(df)

    close_series = df["close"]
    open_series = df["open"]
    dates = df.index

    result.daily_signals = signals

    # ── Warm-up: find first non-NaN indicator bar ────────────────
    warmup = _find_warmup(df, strategy)
    if warmup >= len(close_series):
        return result

    # ── State machine: walk signals → trades ─────────────────────
    in_position = False
    entry_price = 0.0
    entry_date: Optional[pd.Timestamp] = None
    entry_idx = 0
    regime_entry = "unknown"

    trades: list[Trade] = []
    equity = [1.0]

    # Pre-classify regimes for trade-level attribution
    from ..research.regimes import classify_regimes
    trend_reg, _ = classify_regimes(close_series)

    for i in range(warmup, len(close_series)):
        sig = signals.iloc[i]
        cur_close = float(close_series.iloc[i])

        # ── Entry ────────────────────────────────────────────────
        if not in_position and sig == "BUY":
            if i + 1 < len(close_series):
                in_position = True
                entry_price = float(open_series.iloc[i + 1])
                entry_date = dates[i]
                entry_idx = i
                regime_entry = str(trend_reg.iloc[i]) if i < len(trend_reg) else "unknown"

        # ── Exit ─────────────────────────────────────────────────
        if in_position and (sig == "SELL" or i == len(close_series) - 1):
            exit_price = float(open_series.iloc[i])
            exit_date = dates[i]
            ret = (exit_price - entry_price) / entry_price

            trade = Trade(
                entry_date=entry_date or dates[entry_idx],
                exit_date=exit_date,
                entry_price=entry_price,
                exit_price=exit_price,
                bars_held=i - entry_idx,
                return_pct=round(ret * 100, 2),
                exit_reason="signal" if sig == "SELL" else "end_of_data",
                regime_at_entry=regime_entry,
            )
            trades.append(trade)
            in_position = False

        # ── Update equity curve ──────────────────────────────────
        if in_position:
            current_value = 1.0 + (cur_close - entry_price) / entry_price
        else:
            current_value = 1.0 if not trades else (
                1.0 + sum(t.return_pct for t in trades) / 100.0
            )
        equity.append(current_value)

    # ── Compute metrics via research module ──────────────────────
    from ..research.risk_metrics import compute_expanded_metrics

    extra = compute_expanded_metrics(equity, trades, close_series, warmup)

    # ── Regime breakdown ─────────────────────────────────────────
    from ..research.regimes import compute_regime_breakdown
    if trades and len(close_series) > 260:
        regime = compute_regime_breakdown(
            pair, close_series, trades, equity, result.daily_signals,
            warmup=warmup,
        )
        result.regime_breakdown = {
            "dominant_trend": regime.dominant_trend,
            "dominant_vol": regime.dominant_vol,
            "overall_assessment": regime.overall_assessment,
            "total_return_pct": regime.total_return_pct,
            "sharpe_ratio": regime.sharpe_ratio,
            "max_drawdown_pct": regime.max_drawdown_pct,
            "win_rate_pct": regime.win_rate_pct,
            "trade_count": regime.trade_count,
            "time_in_market_pct": regime.time_in_market_pct,
        }

    # Populate result
    result.total_trades = extra.get("total_trades", 0)
    result.winning_trades = extra.get("winning_trades", 0)
    result.losing_trades = extra.get("losing_trades", 0)
    result.total_return_pct = extra.get("total_return_pct", 0.0)
    result.annualised_return_pct = extra.get("annualised_return_pct", 0.0)
    result.max_drawdown_pct = extra.get("max_drawdown_pct", 0.0)
    result.sharpe_ratio = extra.get("sharpe_ratio", 0.0)
    result.sortino_ratio = extra.get("sortino_ratio", 0.0)
    result.profit_factor = extra.get("profit_factor", 0.0)
    result.volatility_pct = extra.get("volatility_pct", 0.0)
    result.calmar_ratio = extra.get("calmar_ratio", 0.0)
    result.avg_return_pct = extra.get("avg_return_pct", 0.0)
    result.avg_win_pct = extra.get("avg_win_pct", 0.0)
    result.avg_loss_pct = extra.get("avg_loss_pct", 0.0)
    result.win_rate_pct = extra.get("win_rate_pct", 0.0)
    result.avg_bars_held = extra.get("avg_bars_held", 0.0)
    result.avg_holding_days = extra.get("avg_holding_days", 0.0)
    result.trade_frequency_per_year = extra.get("trade_frequency_per_year", 0.0)
    result.time_in_market_pct = extra.get("time_in_market_pct", 0.0)

    result.trades = trades
    result.equity_curve = equity
    result.data_start = str(dates[0].date())
    result.data_end = str(dates[-1].date())

    # Buy-and-hold benchmark
    bh_ret = (float(close_series.iloc[-1]) - float(close_series.iloc[warmup])) / float(close_series.iloc[warmup])
    result.buy_and_hold_return_pct = round(bh_ret * 100, 2)

    # Benchmark curve
    bench_slice = close_series.iloc[warmup:]
    result.benchmark_curve = (bench_slice / bench_slice.iloc[0]).tolist()

    return result


def _find_warmup(df: pd.DataFrame, strategy: BaseStrategy) -> int:
    """Find the first bar where all strategy indicators are valid."""
    # Use RSI(14) warmup as a reasonable default (most indicators need 20–200 bars)
    default_warmup = 50
    strategy_name = strategy.name.lower()

    if "sma" in strategy_name and ("200" in strategy_name or "filter" in strategy_name):
        return 200 + 5
    if "sma" in strategy_name:
        return 50 + 5

    return default_warmup
