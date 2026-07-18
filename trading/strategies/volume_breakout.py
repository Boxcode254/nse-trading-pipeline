"""Strategy E — Volume-Confirmed Breakout.

Enters when price breaks above its 20-day high with volume at least
1.5x the 20-day average. Exits after N days or when price drops
below the 20-day low (stop).

Volume confirmation filters false breakouts — a common failure mode
of pure price breakout strategies, especially in low-liquidity markets.
"""
from __future__ import annotations

import pandas as pd

from ..indicators.tech import sma
from .base import BaseStrategy, StrategyMeta


class VolumeBreakout(BaseStrategy):
    """Volume-confirmed breakout: price + volume = conviction."""

    meta = StrategyMeta(
        name="Volume Breakout",
        description="Buy when price breaks 20-day high with 1.5x avg volume. Exit after N days or on stop.",
        params={
            "lookback": 20,
            "volume_multiplier": 1.5,
            "hold_days": 10,
            "stop_pct": 5.0,  # exit if price drops 5% below entry
            "trend_filter_period": 50,  # only take breakouts above SMA(50)
        },
        version="1.0",
    )

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.meta.params
        df["high_20"] = df["high"].rolling(p["lookback"]).max().shift(1)  # yesterday's 20-day high
        df["low_20"] = df["low"].rolling(p["lookback"]).min().shift(1)    # yesterday's 20-day low
        df["volume_sma"] = df["volume"].rolling(p["lookback"]).mean()
        df["sma_50"] = sma(df["close"], p["trend_filter_period"])
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.meta.params
        signals = pd.Series("HOLD", index=df.index, dtype="object")

        in_position = False
        bars_held = 0
        entry_price = 0.0

        for i in range(len(df)):
            row = df.iloc[i]
            close = row["close"]
            high20 = row["high_20"]
            low20 = row["low_20"]
            vol = row["volume"]
            vol_sma = row["volume_sma"]
            sma50 = row["sma_50"]

            if pd.isna(high20) or pd.isna(vol_sma) or pd.isna(sma50):
                continue

            # Valid volume data
            has_volume = vol_sma > 0 and not pd.isna(vol)
            volume_surge = has_volume and vol >= vol_sma * p["volume_multiplier"]

            # Entry: close breaks above yesterday's 20-day high with volume surge, above SMA(50) trend
            breakout = close > high20 and volume_surge and close > sma50

            # Exit conditions
            time_stop = in_position and bars_held >= p["hold_days"]
            price_stop = in_position and close < entry_price * (1 - p["stop_pct"] / 100)
            trend_stop = in_position and close < sma50 * 0.95

            if in_position:
                bars_held += 1

            if not in_position and breakout:
                signals.iloc[i] = "BUY"
                in_position = True
                bars_held = 0
                entry_price = close
            elif in_position and (time_stop or price_stop or trend_stop):
                signals.iloc[i] = "SELL"
                in_position = False
                bars_held = 0

        return signals
