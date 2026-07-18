"""Generate BUY / SELL / HOLD signals from OHLCV data.

The signal logic intentionally mirrors the spec exactly so the engine is
predictable and easy to read:

    SMA(20) crosses above SMA(50) AND RSI(14) > 50  -> BUY
    SMA(20) crosses below SMA(50) AND RSI(14) < 50  -> SELL
    otherwise                                       -> HOLD

generate_signals returns *every* bar (with its verdict) for the backtest to
consume; cmd_run() filters down to today's single signal for live use.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .. import config
from ..indicators import tech


def _series_signals(
    df: pd.DataFrame,
    sma_fast: pd.Series,
    sma_slow: pd.Series,
    rsi_series: pd.Series,
) -> list[dict[str, Any]]:
    """Walk every bar and emit a per-bar verdict.

    A "cross" only fires on the bar where the relationship flips relative to
    the previous bar -- this is the standard crossover definition and avoids
    counting every bar where fast > slow as a BUY.
    """
    signals: list[dict[str, Any]] = []
    closes = df["close"].values
    dates = df.index

    prev_fast = sma_fast.shift(1)
    prev_slow = sma_slow.shift(1)

    for i, ts in enumerate(dates):
        f = sma_fast.iloc[i]
        s = sma_slow.iloc[i]
        r = rsi_series.iloc[i]
        pf = prev_fast.iloc[i] if not pd.isna(prev_fast.iloc[i]) else f
        ps = prev_slow.iloc[i] if not pd.isna(prev_slow.iloc[i]) else s

        if pd.isna(f) or pd.isna(s) or pd.isna(r):
            verdict = "HOLD"
        elif f > s and pf <= ps and r > 50.0:
            verdict = "BUY"
        elif f < s and pf >= ps and r < 50.0:
            verdict = "SELL"
        else:
            verdict = "HOLD"

        signals.append(
            {
                "pair": "",  # filled in by the caller
                "date": pd.Timestamp(ts).strftime("%Y-%m-%d"),
                "signal": verdict,
                "price": float(closes[i]),
                "sma_fast": float(f) if not pd.isna(f) else float("nan"),
                "sma_slow": float(s) if not pd.isna(s) else float("nan"),
                "rsi": float(r) if not pd.isna(r) else float("nan"),
            }
        )
    return signals


def generate_signals(df: pd.DataFrame, pair: str = "") -> list[dict[str, Any]]:
    """Compute indicators over ``df`` and return a per-bar signal list.

    The DataFrame must have a 'close' column and a DatetimeIndex.
    """
    if "close" not in df.columns:
        raise ValueError("DataFrame must have a 'close' column")

    close = df["close"]
    sma_fast = tech.sma(close, config.SMA_FAST)
    sma_slow = tech.sma(close, config.SMA_SLOW)
    rsi_series = tech.rsi(close, config.RSI_PERIOD)

    signals = _series_signals(df, sma_fast, sma_slow, rsi_series)
    for s in signals:
        s["pair"] = pair
    return signals


def latest_signal(df: pd.DataFrame, pair: str) -> dict[str, Any]:
    """Return just the most recent signal in the series."""
    signals = generate_signals(df, pair=pair)
    return signals[-1]
