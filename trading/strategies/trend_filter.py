"""Strategy C — SMA(20/50) + RSI(14) with SMA(200) trend filter.

Only takes long trades when price is above SMA(200) — i.e.,
only trade in the direction of the primary trend.

This is the first research experiment: does a simple trend filter
improve the baseline strategy?
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..indicators.tech import sma, rsi
from .base import BaseStrategy, StrategyMeta


class TrendFilteredSma(BaseStrategy):
    """SMA(20/50) crossover with SMA(200) trend filter — no short signals below SMA(200)."""

    meta = StrategyMeta(
        name="SMA(20/50) + SMA(200) filter",
        description="Baseline SMA crossover but only takes long signals when price > SMA(200)",
        params={
            "sma_fast": config.SMA_FAST,       # 20
            "sma_slow": config.SMA_SLOW,        # 50
            "sma_trend": 200,                    # long-term trend
            "rsi_period": config.RSI_PERIOD,    # 14
            "rsi_entry_min": 40.0,
            "rsi_exit_max": 70.0,
        },
        version="1.0",
    )

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.meta.params
        df["sma_fast"] = sma(df["close"], p["sma_fast"])
        df["sma_slow"] = sma(df["close"], p["sma_slow"])
        df["sma_trend"] = sma(df["close"], p["sma_trend"])
        df["rsi"] = rsi(df["close"], p["rsi_period"])
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.meta.params
        signals = pd.Series("HOLD", index=df.index, dtype="object")
        prev_fast = df["sma_fast"].shift(1)
        prev_slow = df["sma_slow"].shift(1)

        in_position = False
        for i in range(len(df)):
            row = df.iloc[i]
            close = row["close"]
            f, s, trend, r = row["sma_fast"], row["sma_slow"], row["sma_trend"], row["rsi"]
            if pd.isna(f) or pd.isna(s) or pd.isna(trend) or pd.isna(r):
                continue

            pf = prev_fast.iloc[i] if not pd.isna(prev_fast.iloc[i]) else f
            ps = prev_slow.iloc[i] if not pd.isna(prev_slow.iloc[i]) else s

            # Trend filter: price must be above SMA(200) to go long
            uptrend = close > trend

            bullish_cross = pf <= ps and f > s
            bearish_cross = pf >= ps and f < s

            if not in_position and bullish_cross and r > p["rsi_entry_min"] and uptrend:
                signals.iloc[i] = "BUY"
                in_position = True
            elif in_position and (bearish_cross or r > p["rsi_exit_max"]):
                signals.iloc[i] = "SELL"
                in_position = False

        return signals
