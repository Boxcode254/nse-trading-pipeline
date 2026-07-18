"""Technical indicators. Pure pandas -- no heavy TA libs.

Exports
-------
sma(series, period)        -- simple moving average
rsi(series, period)        -- Wilder's RSI
crossover(fast, slow)      -- last-bar relationship: bullish / bearish / neutral
"""
from __future__ import annotations

import pandas as pd


def sma(data: pd.Series, period: int) -> pd.Series:
    """Simple moving average. Returns NaN for the first ``period - 1`` bars."""
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period}")
    return data.rolling(window=period, min_periods=period).mean()


def rsi(data: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. Returns values in [0, 100]."""
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period}")
    delta = data.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder smoothing: equivalent to an EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # Convention: when there are no losses, RSI = 100
    out = out.where(avg_loss != 0, 100.0)
    return out


def crossover(fast: pd.Series, slow: pd.Series) -> str:
    """Classify the latest relationship between two series.

    - "bullish"  : fast just crossed above slow (last bar only)
    - "bearish"  : fast just crossed below slow (last bar only)
    - "neutral"  : everything else (including insufficient history)
    """
    if len(fast) < 2 or len(slow) < 2:
        return "neutral"
    a, b = fast.iloc[-1], slow.iloc[-1]
    a_prev, b_prev = fast.iloc[-2], slow.iloc[-2]
    if any(pd.isna(x) for x in (a, b, a_prev, b_prev)):
        return "neutral"
    if a > b and a_prev <= b_prev:
        return "bullish"
    if a < b and a_prev >= b_prev:
        return "bearish"
    return "neutral"
