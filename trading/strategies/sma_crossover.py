"""Strategy A — SMA(20/50) + RSI(14) crossover.

This is the baseline / benchmark strategy.  It is **frozen**:
no parameter tuning, no modifications.  All future strategies
are compared against this one.
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..indicators.tech import sma, rsi, crossover
from .base import BaseStrategy, StrategyMeta


class SmaCrossover(BaseStrategy):
    """SMA(20/50) crossover with RSI(14) confirmation — benchmark."""

    meta = StrategyMeta(
        name="SMA(20/50) + RSI(14)",
        description="SMA(20/50) crossover confirmed by RSI(14) > 40 entry, RSI > 70 exit",
        params={
            "sma_fast": config.SMA_FAST,       # 20
            "sma_slow": config.SMA_SLOW,        # 50
            "rsi_period": config.RSI_PERIOD,    # 14
            "rsi_entry_min": 40.0,
            "rsi_exit_max": 70.0,
        },
        version="1.0",
    )

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df["sma_fast"] = sma(df["close"], self.meta.params["sma_fast"])
        df["sma_slow"] = sma(df["close"], self.meta.params["sma_slow"])
        df["rsi"] = rsi(df["close"], self.meta.params["rsi_period"])
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        fast = self.meta.params["sma_fast"]
        slow = self.meta.params["sma_slow"]
        rsi_entry = self.meta.params["rsi_entry_min"]
        rsi_exit = self.meta.params["rsi_exit_max"]

        signals = pd.Series("HOLD", index=df.index, dtype="object")
        prev_fast = df["sma_fast"].shift(1)
        prev_slow = df["sma_slow"].shift(1)

        in_position = False
        for i in range(len(df)):
            row = df.iloc[i]
            f, s, r = row["sma_fast"], row["sma_slow"], row["rsi"]
            if pd.isna(f) or pd.isna(s) or pd.isna(r):
                continue

            pf = prev_fast.iloc[i] if not pd.isna(prev_fast.iloc[i]) else f
            ps = prev_slow.iloc[i] if not pd.isna(prev_slow.iloc[i]) else s

            bullish_cross = pf <= ps and f > s
            bearish_cross = pf >= ps and f < s

            if not in_position and bullish_cross and r > rsi_entry:
                signals.iloc[i] = "BUY"
                in_position = True
            elif in_position and (bearish_cross or r > rsi_exit):
                signals.iloc[i] = "SELL"
                in_position = False

        return signals
