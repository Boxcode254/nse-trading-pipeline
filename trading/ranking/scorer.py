"""Factor scoring for the Market Ranking Engine.

Each public ``score_*`` function takes an OHLCV DataFrame (with a
DatetimeIndex named 'date' and OHLCV columns) plus any factor-specific
parameters, and returns a 0-100 score.

A higher score = more attractive for an accumulator. Each function is
pure, deterministic, and tolerant of short histories (returns a
neutral 50.0 when there's not enough data).

Scoring philosophy
------------------
- Scores are bounded to [0, 100] for clean aggregation.
- Each factor is computed independently; aggregation is a weighted
  mean (see :func:`aggregate_score`).
- No min-max normalisation against the universe — the values are
  interpretable on their own (trend 70 = "trending up", not "70th
  percentile of the universe"). Relative strength is the one
  exception: it explicitly compares against the universe.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

from ..indicators import tech
from ..research.regimes import classify_regimes
from ..research.risk_metrics import annualised_volatility


# Order of factors, used by aggregation and reports.
SCORE_FACTOR_NAMES: list[str] = [
    "trend",
    "momentum",
    "volatility",
    "liquidity",
    "relative_strength",
    "risk",
    "regime",
    "alignment",
]


# ── Helpers ──────────────────────────────────────────────────────────


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp a value into [lo, hi]."""
    if x is None or np.isnan(x):
        return 50.0  # neutral
    return float(max(lo, min(hi, x)))


def _linear(x: float, x0: float, x1: float,
            y0: float = 0.0, y1: float = 100.0) -> float:
    """Linear interpolation from x ∈ [x0, x1] → y ∈ [y0, y1], clamped.

    Used throughout to convert a raw indicator value into a 0-100 score.
    """
    if x1 == x0:
        return (y0 + y1) / 2
    y = y0 + (x - x0) * (y1 - y0) / (x1 - x0)
    return _clamp(y)


# ── 1. Trend ─────────────────────────────────────────────────────────


def score_trend(
    df: pd.DataFrame,
    sma_fast: int = 20,
    sma_slow: int = 50,
) -> float:
    """SMA alignment + slope direction.

    Strong uptrend (SMA20 well above SMA50, positive slope) → high
    score. Strong downtrend → low. Sideways → neutral.
    """
    if df is None or len(df) < sma_slow + 5 or "close" not in df.columns:
        return 50.0

    close = df["close"]
    sma_f = tech.sma(close, sma_fast)
    sma_s = tech.sma(close, sma_slow)
    if sma_f.isna().all() or sma_s.isna().all():
        return 50.0

    f = sma_f.iloc[-1]
    s = sma_s.iloc[-1]
    if pd.isna(f) or pd.isna(s) or float(s) == 0:
        return 50.0

    # SMA gap (% of slow SMA) — typical range: -5% to +5%
    gap_pct = (float(f) - float(s)) / float(s) * 100.0
    alignment = _linear(gap_pct, x0=-5.0, x1=5.0)

    # Slope of slow SMA over the last 20 bars
    sma_s_recent = sma_s.dropna().tail(20)
    if len(sma_s_recent) < 5:
        slope_part = 50.0
    else:
        first = float(sma_s_recent.iloc[0])
        last = float(sma_s_recent.iloc[-1])
        slope_pct = (last - first) / first * 100.0 if first != 0 else 0.0
        slope_part = _linear(slope_pct, x0=-5.0, x1=5.0)

    return _clamp(0.6 * alignment + 0.4 * slope_part)


# ── 2. Momentum ──────────────────────────────────────────────────────


def score_momentum(df: pd.DataFrame, rsi_period: int = 14) -> float:
    """RSI(14) + 5d/20d rate of change.

    For an accumulator, RSI in the 55-75 range is ideal — strong but
    not overbought. RSI > 80 = euphoria (caution). RSI < 30 = washed
    out (interesting entry for a contrarian). We blend RSI with ROC.
    """
    if df is None or len(df) < max(rsi_period + 5, 25) or "close" not in df.columns:
        return 50.0

    close = df["close"]
    rsi_series = tech.rsi(close, rsi_period)
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.isna().all() else 50.0

    # 5d and 20d rate of change
    last = float(close.iloc[-1])
    roc5 = (last / float(close.iloc[-6]) - 1) * 100 if len(close) > 6 else 0.0
    roc20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0.0

    # RSI scoring: 50 = neutral, 65 = strong, 80 = extreme
    # Slightly penalise overbought (RSI > 75)
    if rsi <= 50:
        rsi_score = _linear(rsi, x0=20.0, x1=50.0)  # 20→0, 50→100
    elif rsi <= 75:
        rsi_score = _linear(rsi, x0=50.0, x1=75.0)  # 50→100, 75→100
    else:
        # RSI > 75 → start to fade (overbought)
        rsi_score = _linear(rsi, x0=75.0, x1=90.0, y0=100.0, y1=40.0)

    roc5_score = _linear(roc5, x0=-3.0, x1=5.0)
    roc20_score = _linear(roc20, x0=-8.0, x1=12.0)

    return _clamp(0.5 * rsi_score + 0.25 * roc5_score + 0.25 * roc20_score)


# ── 3. Volatility ────────────────────────────────────────────────────


def score_volatility(df: pd.DataFrame) -> float:
    """Annualised volatility (lower = more attractive for accumulators).

    Typical ann. vol ranges: 8% (calm FX) → 60% (crypto). Stocks
    usually 15-30%. The score is highest for ann. vol around 10-15%
    (steady) and lower for both very low (dead) and very high (risky)
    volatility. This is the "stability score" the spec asks for.
    """
    if df is None or len(df) < 30 or "close" not in df.columns:
        return 50.0

    close = df["close"]
    daily_ret = close.pct_change().dropna().to_numpy()
    if len(daily_ret) < 5:
        return 50.0
    ann_vol_pct = annualised_volatility(daily_ret) * 100.0  # in %

    # Sweet spot: 10-20% → 100. Below 5% → 60. Above 40% → 0.
    if ann_vol_pct <= 5:
        return _linear(ann_vol_pct, x0=0.0, x1=5.0, y0=40.0, y1=60.0)
    if ann_vol_pct <= 20:
        return _linear(ann_vol_pct, x0=5.0, x1=20.0, y0=60.0, y1=100.0)
    return _linear(ann_vol_pct, x0=20.0, x1=50.0, y0=100.0, y1=0.0)


# ── 4. Liquidity ─────────────────────────────────────────────────────


def score_liquidity(df: pd.DataFrame) -> float:
    """Recent volume vs trailing history.

    Compares the last 20d average volume to the trailing 100d
    average. A 1.0x ratio is neutral. > 1.5x = strong current
    interest. < 0.5x = illiquid / losing attention.
    """
    if df is None or len(df) < 30 or "volume" not in df.columns:
        return 50.0

    vol = pd.to_numeric(df["volume"], errors="coerce").dropna()
    if len(vol) < 30:
        return 50.0

    recent = vol.tail(20).mean()
    baseline = vol.tail(min(len(vol), 100)).mean()
    if baseline <= 0 or np.isnan(baseline) or np.isnan(recent):
        return 50.0

    ratio = recent / baseline
    # 0.3x → 20, 1.0x → 60, 2.0x → 100
    if ratio <= 1.0:
        return _linear(ratio, x0=0.3, x1=1.0, y0=20.0, y1=60.0)
    return _linear(ratio, x0=1.0, x1=2.0, y0=60.0, y1=100.0)


# ── 5. Relative Strength ─────────────────────────────────────────────


def score_relative_strength(
    df: pd.DataFrame,
    universe: Optional[dict[str, pd.DataFrame]] = None,
    windows: Sequence[int] = (21, 63),
) -> float:
    """Outperformance vs the rest of the universe over 1m/3m.

    If ``universe`` is None, falls back to the input frame vs itself
    (returns 50.0 — neutral). This makes the function safe to call
    without a universe context; the ranker wires it up properly.
    """
    if df is None or len(df) < max(windows) + 5 or "close" not in df.columns:
        return 50.0
    if not universe or len(universe) < 2:
        return 50.0

    close = df["close"].astype(float)
    beats = []
    for other_sym, other_df in universe.items():
        if other_df is df or other_df is None or "close" not in other_df.columns:
            continue
        other_close = other_df["close"].astype(float)
        for w in windows:
            if len(close) <= w or len(other_close) <= w:
                continue
            mine = float(close.iloc[-1]) / float(close.iloc[-w - 1]) - 1
            theirs = float(other_close.iloc[-1]) / float(other_close.iloc[-w - 1]) - 1
            beats.append(1.0 if mine > theirs else 0.0)

    if not beats:
        return 50.0
    win_rate = sum(beats) / len(beats)
    # 0% win rate → 0, 50% → 50, 100% → 100
    return _clamp(win_rate * 100.0)


# ── 6. Risk ──────────────────────────────────────────────────────────


def score_risk(df: pd.DataFrame, lookback: int = 90) -> float:
    """Max drawdown over the lookback window + downside deviation.

    Small drawdowns (less than 5%) score high. Big drawdowns (more
    than 30%) score low. Calmar-like intuition: low realised risk
    with positive trend = friendly to an accumulator.
    """
    if df is None or len(df) < 20 or "close" not in df.columns:
        return 50.0

    close = df["close"].astype(float).tail(lookback)
    if len(close) < 10:
        return 50.0

    # Max drawdown
    peak = close.cummax()
    drawdown = (close - peak) / peak
    max_dd_pct = abs(float(drawdown.min())) * 100.0 if not drawdown.isna().all() else 0.0

    # Downside deviation (annualised)
    daily_ret = close.pct_change().dropna()
    downside = daily_ret[daily_ret < 0]
    if len(downside) >= 5 and downside.std() > 0:
        downside_dev_pct = float(downside.std() * np.sqrt(252) * 100.0)
    else:
        downside_dev_pct = 0.0

    # Drawdown component: 0% dd → 100, 30% dd → 0
    dd_score = _linear(max_dd_pct, x0=0.0, x1=30.0, y0=100.0, y1=0.0)
    # Downside component: 0% → 100, 25% → 0
    ds_score = _linear(downside_dev_pct, x0=0.0, x1=25.0, y0=100.0, y1=0.0)

    return _clamp(0.6 * dd_score + 0.4 * ds_score)


# ── 7. Market Regime ────────────────────────────────────────────────


def score_regime(df: pd.DataFrame) -> float:
    """Regime classification (uses research/regimes.classify_regimes).

    Bull + Low Vol → 100 (best). Bull + High Vol → 80.
    Sideways + Low Vol → 50. Bear + High Vol → 0 (worst).
    """
    if df is None or len(df) < 260 or "close" not in df.columns:
        return 50.0

    close = df["close"].astype(float)
    try:
        trend_reg, vol_reg = classify_regimes(close)
    except Exception:
        return 50.0
    if len(trend_reg) == 0:
        return 50.0

    last_trend = trend_reg.iloc[-1]
    last_vol = vol_reg.iloc[-1] if len(vol_reg) else "Low Vol"

    # Base score by trend
    if last_trend == "Bull":
        base = 80.0
    elif last_trend == "Sideways":
        base = 50.0
    else:  # Bear
        base = 20.0

    # Adjust by volatility: low vol is friendly, high vol is hostile
    if last_vol == "Low Vol":
        adj = +15.0
    else:  # High Vol
        adj = -15.0

    return _clamp(base + adj)


# ── 8. Technical Alignment ──────────────────────────────────────────


def score_alignment(df: pd.DataFrame) -> float:
    """How many of the 5 internal indicators agree on direction.

    Indicators polled: SMA(20) > SMA(50), close > SMA(20), close >
    SMA(50), RSI(14) > 50, 20d ROC > 0. 5/5 = strong agreement
    (100). 0/5 = strong disagreement (0). 2-3/5 = mixed (50).
    """
    if df is None or len(df) < 50 or "close" not in df.columns:
        return 50.0

    close = df["close"].astype(float)
    sma_f = tech.sma(close, 20).iloc[-1]
    sma_s = tech.sma(close, 50).iloc[-1]
    rsi_s = tech.rsi(close, 14).iloc[-1]
    last = float(close.iloc[-1])

    votes: list[bool] = []
    if not pd.isna(sma_f) and not pd.isna(sma_s):
        votes.append(bool(sma_f > sma_s))
    if not pd.isna(sma_f):
        votes.append(bool(last > sma_f))
    if not pd.isna(sma_s):
        votes.append(bool(last > sma_s))
    if not pd.isna(rsi_s):
        votes.append(bool(rsi_s > 50.0))
    if len(close) > 20:
        roc = (last / float(close.iloc[-21]) - 1) > 0
        votes.append(bool(roc))

    if not votes:
        return 50.0
    return _clamp(sum(votes) / len(votes) * 100.0)


# ── Aggregation ──────────────────────────────────────────────────────


def aggregate_score(
    factor_scores: dict[str, float],
    weights: Optional[dict[str, float]] = None,
) -> float:
    """Combine the 8 factor scores into a single 0-100 score.

    Default weights are taken from ``config.SCORING_WEIGHTS``. A
    caller can pass a custom dict for sensitivity analysis. Missing
    factors default to 50 (neutral).
    """
    from .. import config

    if weights is None:
        weights = config.SCORING_WEIGHTS

    total = 0.0
    total_w = 0.0
    for name in SCORE_FACTOR_NAMES:
        w = float(weights.get(name, 0.0))
        if w <= 0:
            continue
        score = float(factor_scores.get(name, 50.0))
        # Guard against NaN
        if np.isnan(score):
            score = 50.0
        total += w * _clamp(score)
        total_w += w
    if total_w <= 0:
        return 50.0
    return _clamp(total / total_w)


def score_all_factors(
    df: pd.DataFrame,
    universe: Optional[dict[str, pd.DataFrame]] = None,
    sma_fast: int = 20,
    sma_slow: int = 50,
    rsi_period: int = 14,
    risk_lookback: int = 90,
) -> dict[str, float]:
    """Compute all 8 factor scores in one call. Convenience wrapper."""
    return {
        "trend": score_trend(df, sma_fast, sma_slow),
        "momentum": score_momentum(df, rsi_period),
        "volatility": score_volatility(df),
        "liquidity": score_liquidity(df),
        "relative_strength": score_relative_strength(df, universe),
        "risk": score_risk(df, lookback=risk_lookback),
        "regime": score_regime(df),
        "alignment": score_alignment(df),
    }
