"""Strategy D — Momentum Trend Following.

Buys when price is above SMA(50) and 20-day return is positive.
Sells when price drops below SMA(50) OR 20-day return turns negative.
Stays in position through minor dips — only exits on trend break.

This avoids the whipsaw problem of SMA crossover by using the long-term
moving average as a trend filter and momentum as an entry/exit gate.
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..indicators.tech import sma, rsi
from .base import BaseStrategy, StrategyMeta


class MomentumTrend(BaseStrategy):
    """Trend-following momentum: buy uptrends, hold through dips, exit on trend break."""

    meta = StrategyMeta(
        name="Momentum Trend",
        description="Buy when price > SMA(50) and 20-day return positive. Exit on trend break.",
        params={
            "sma_period": 50,
            "momentum_window": 20,
            "momentum_threshold": 0.0,  # positive return = uptrend
            "rsi_period": config.RSI_PERIOD,  # 14
            "rsi_oversold": 30.0,  # extra buy signal on oversold bounces
        },
        version="1.0",
    )

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.meta.params
        df["sma_50"] = sma(df["close"], p["sma_period"])
        df["momentum"] = df["close"].pct_change(p["momentum_window"]) * 100
        df["rsi"] = rsi(df["close"], p["rsi_period"])
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.meta.params
        signals = pd.Series("HOLD", index=df.index, dtype="object")

        in_position = False
        for i in range(len(df)):
            row = df.iloc[i]
            close = row["close"]
            sma50 = row["sma_50"]
            momentum = row["momentum"]
            rsi_val = row["rsi"]

            if pd.isna(sma50) or pd.isna(momentum) or pd.isna(rsi_val):
                continue

            # Entry: price above SMA(50) + positive momentum
            uptrend = close > sma50 and momentum > p["momentum_threshold"]
            # Extra entry: oversold bounce in an uptrend (price just crossed above SMA)
            dip_buy = rsi_val < p["rsi_oversold"] and close > sma50 * 0.95

            # Exit: price drops below SMA(50) or momentum turns very negative
            trend_broken = close < sma50 or momentum < -5.0

            if not in_position and (uptrend or dip_buy):
                signals.iloc[i] = "BUY"
                in_position = True
            elif in_position and trend_broken:
                signals.iloc[i] = "SELL"
                in_position = False

        return signals
