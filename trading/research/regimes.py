"""Market regime classifier.

Divides a price DataFrame into rolling windows and classifies each
window into one of five regimes:

- **Bull** — strong positive trend
- **Bear** — strong negative trend
- **Sideways** — low absolute trend
- **High volatility** — above-average volatility (can overlay any trend)
- **Low volatility** — below-average volatility

Used by the research module to answer: "where does this strategy
work and where does it fail?"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ── Regime labels ─────────────────────────────────────────────────

TREND_BULL = "Bull"
TREND_BEAR = "Bear"
TREND_SIDEWAYS = "Sideways"
VOL_HIGH = "High Vol"
VOL_LOW = "Low Vol"

ALL_REGIMES = [TREND_BULL, TREND_BEAR, TREND_SIDEWAYS, VOL_HIGH, VOL_LOW]


@dataclass
class RegimeBreakdown:
    """Per-regime performance breakdown for a strategy backtest.

    Each attribute is a dict keyed by regime label (``\"Bull\"``, etc.).
    """
    total_return_pct: dict[str, float] = field(default_factory=dict)
    sharpe_ratio: dict[str, float] = field(default_factory=dict)
    max_drawdown_pct: dict[str, float] = field(default_factory=dict)
    win_rate_pct: dict[str, float] = field(default_factory=dict)
    trade_count: dict[str, int] = field(default_factory=dict)
    avg_bars_held: dict[str, float] = field(default_factory=dict)
    time_in_market_pct: dict[str, float] = field(default_factory=dict)
    # Summary
    dominant_trend: str = ""
    dominant_vol: str = ""
    overall_assessment: str = ""


def classify_regimes(
    close_series: pd.Series,
    trend_window: int = 200,
    vol_window: int = 60,
) -> tuple[pd.Series, pd.Series]:
    """Classify every bar into a trend regime and a volatility regime.

    Parameters
    ----------
    close_series : pd.Series
        Daily close prices (DatetimeIndex).
    trend_window : int
        Lookback for trend direction (SMA slope over this window).
    vol_window : int
        Lookback for volatility (rolling std of daily returns).

    Returns
    -------
    trend_regime : pd.Series
        ``\"Bull\"``, ``\"Bear\"``, or ``\"Sideways\"`` per bar.
    vol_regime : pd.Series
        ``\"High Vol\"`` or ``\"Low Vol\"`` per bar.
    """
    # ── Trend regime via SMA slope ────────────────────────────────
    sma_trend = close_series.rolling(trend_window, min_periods=trend_window).mean()
    # Slope of the SMA: percentage change over the window
    pct_change = sma_trend.pct_change(trend_window)
    # Annualised trend strength
    trend_strength = pct_change * (252 / trend_window)

    trend_regime = pd.Series(TREND_SIDEWAYS, index=close_series.index, dtype="object")
    trend_regime[trend_strength > 0.05] = TREND_BULL      # > 5% annualised
    trend_regime[trend_strength < -0.05] = TREND_BEAR     # < -5% annualised
    # NaN bars (before SMA is ready) → Sideways
    trend_regime[trend_strength.isna()] = TREND_SIDEWAYS

    # ── Volatility regime ─────────────────────────────────────────
    daily_ret = close_series.pct_change().dropna()
    rolling_vol = daily_ret.rolling(vol_window, min_periods=vol_window).std()
    # Annualised vol
    ann_vol = rolling_vol * np.sqrt(252)
    median_vol = ann_vol.median()

    vol_regime = pd.Series(VOL_LOW, index=close_series.index, dtype="object")
    # Use .reindex to align; only apply where ann_vol is non-NaN
    high_vol_mask = ann_vol.reindex(close_series.index, method=None) > median_vol * 1.25
    high_vol_mask = high_vol_mask.fillna(False)
    vol_regime[high_vol_mask] = VOL_HIGH

    return trend_regime, vol_regime


def compute_regime_breakdown(
    pair: str,
    close_series: pd.Series,
    trades: list,
    equity_curve: list[float],
    daily_signals: Optional[pd.Series] = None,
    warmup: int = 50,
) -> RegimeBreakdown:
    """Split backtest performance by market regime.

    Parameters
    ----------
    close_series : pd.Series
        Full close price series (same index as backtest input).
    trades : list[Trade]
        List of completed trades from the backtest engine.
    equity_curve : list[float]
        Normalised equity curve (1.0 at start).
    daily_signals : pd.Series, optional
        Signal per bar (BUY/SELL/HOLD) to compute time-in-market.

    Returns
    -------
    RegimeBreakdown
    """
    breakdown = RegimeBreakdown()
    if len(close_series) < 260:  # need ~1 year for meaningful regime data
        breakdown.overall_assessment = "Insufficient data for regime analysis (< 1 year)"
        return breakdown

    trend_reg, vol_reg = classify_regimes(close_series)

    # Subset to the backtest period only (warmup → end)  
    if len(close_series) > warmup:
        trend_reg = trend_reg.iloc[warmup:]
        close_series = close_series.iloc[warmup:]

    # Compute dominant regimes
    breakdown.dominant_trend = trend_reg.value_counts().idxmax()
    breakdown.dominant_vol = vol_reg.value_counts().idxmax()

    # ── Pre-compute equity returns per bar ───────────────────────
    if equity_curve and len(equity_curve) > 1:
        eq = np.array(equity_curve)
        # Trim/pad to match close_series length exactly
        target_len = len(close_series)
        if len(eq) > target_len:
            eq = eq[:target_len]
        eq_returns = np.diff(eq) / eq[:-1]
        eq_returns_padded = np.insert(eq_returns, 0, 0.0)
        # Ensure exact match
        if len(eq_returns_padded) > target_len:
            eq_returns_padded = eq_returns_padded[:target_len]
        elif len(eq_returns_padded) < target_len:
            eq_returns_padded = np.pad(eq_returns_padded, (0, target_len - len(eq_returns_padded)), 'edge')
    else:
        eq_returns_padded = np.zeros(len(close_series))

    # ── Time-in-market per bar ───────────────────────────────────
    time_in_market = None
    if daily_signals is not None:
        # Subset to match the backtest period
        daily_subset = daily_signals.iloc[warmup:] if len(daily_signals) > warmup else daily_signals
        time_in_market = (daily_subset == "BUY").astype(float)

    # ── Per-regime metrics ───────────────────────────────────────
    for regime in [TREND_BULL, TREND_BEAR, TREND_SIDEWAYS]:
        mask = trend_reg.values == regime
        regime_idx = np.where(mask)[0]
        if len(regime_idx) < 10:
            continue

        # Return contribution during this regime
        regime_returns = eq_returns_padded[mask]
        breakdown.total_return_pct[regime] = round(
            float(np.sum(regime_returns) * 100), 2
        )

        # Sharpe within regime
        if regime_returns.std() > 0:
            ann_factor = np.sqrt(252)
            breakdown.sharpe_ratio[regime] = round(
                float(np.mean(regime_returns) / np.std(regime_returns) * ann_factor), 2
            )

        # Max drawdown within regime
        if len(regime_idx) > 1:
            eq_slice = np.array(equity_curve[regime_idx[0]:regime_idx[-1] + 1]) \
                if len(equity_curve) > regime_idx[-1] else np.array(equity_curve)
            if len(eq_slice) > 1:
                peak = np.maximum.accumulate(eq_slice)
                dd = (eq_slice - peak) / peak
                breakdown.max_drawdown_pct[regime] = round(
                    abs(float(np.min(dd))) * 100, 2
                )

        # Time in market
        if time_in_market is not None:
            tm = time_in_market.values[mask]
            breakdown.time_in_market_pct[regime] = round(
                float(np.mean(tm) * 100), 1
            )

    # ── Trade-based metrics per regime ────────────────────────────
    for regime in [TREND_BULL, TREND_BEAR, TREND_SIDEWAYS]:
        regime_trades = [
            t for t in trades
            if t.entry_date in close_series.index and
            trend_reg.loc[t.entry_date] == regime
        ]
        if not regime_trades:
            continue

        breakdown.trade_count[regime] = len(regime_trades)
        rets = [t.return_pct for t in regime_trades]
        breakdown.win_rate_pct[regime] = round(
            sum(1 for r in rets if r > 0) / len(rets) * 100, 1
        )
        breakdown.avg_bars_held[regime] = round(
            float(np.mean([t.bars_held for t in regime_trades])), 1
        )

    # ── Overall assessment ───────────────────────────────────────
    bull_ret = breakdown.total_return_pct.get(TREND_BULL, 0)
    bear_ret = breakdown.total_return_pct.get(TREND_BEAR, 0)
    side_ret = breakdown.total_return_pct.get(TREND_SIDEWAYS, 0)
    parts = []
    if bear_ret > bull_ret:
        parts.append("Performs better in bear markets than bull — suggests crash protection")
    elif bull_ret > bear_ret:
        parts.append("Better in bull markets (trend following captures rallies)")
    if side_ret < max(bull_ret, bear_ret):
        parts.append("Struggles in sideways markets (whipsaw)")
    if breakdown.max_drawdown_pct.get(TREND_BEAR, 100) < breakdown.max_drawdown_pct.get(TREND_BULL, 0):
        parts.append("Drawdowns are contained during bears — risk management working")
    breakdown.overall_assessment = " | ".join(parts) if parts else "Mixed — needs more data"

    return breakdown
