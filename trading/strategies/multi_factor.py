"""Strategy F — Multi-Factor Composite.

Scores each bar on 4 dimensions (trend, momentum, volatility, volume)
and generates BUY/SELL based on a composite threshold.

Unlike SMA crossover which only looks at two moving averages, this
combines multiple independent signals — each contributing partial
evidence. The composite is less prone to false signals because
one weak factor can't trigger a trade alone.
"""
from __future__ import annotations

import pandas as pd

from ..indicators.tech import sma, rsi
from .base import BaseStrategy, StrategyMeta


class MultiFactor(BaseStrategy):
    """4-factor composite scoring: trend + momentum + volatility + volume."""

    meta = StrategyMeta(
        name="Multi-Factor Composite",
        description="Score on trend, momentum, volatility, volume. Buy when composite > 60, sell < 30.",
        params={
            # Trend weights
            "sma_fast": 20,
            "sma_slow": 50,
            "trend_weight": 0.30,
            # Momentum
            "mom_short": 10,
            "mom_long": 40,
            "momentum_weight": 0.30,
            # Volatility (lower is better for long positions)
            "vol_window": 20,
            "volatility_weight": 0.15,
            # Volume confirmation
            "volume_window": 20,
            "volume_weight": 0.25,
            # Thresholds
            "buy_threshold": 60.0,
            "sell_threshold": 30.0,
            # RSI filter
            "rsi_period": 14,
            "rsi_oversold": 30.0,
        },
        version="1.0",
    )

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.meta.params
        # Trend
        df["sma_fast"] = sma(df["close"], p["sma_fast"])
        df["sma_slow"] = sma(df["close"], p["sma_slow"])
        # Momentum
        df["mom_short"] = df["close"].pct_change(p["mom_short"])
        df["mom_long"] = df["close"].pct_change(p["mom_long"])
        # Volatility
        df["returns"] = df["close"].pct_change()
        df["volatility"] = df["returns"].rolling(p["vol_window"]).std()
        # Volume
        df["volume_sma"] = df["volume"].rolling(p["volume_window"]).mean()
        df["volume_ratio"] = df["volume"] / df["volume_sma"]
        # RSI
        df["rsi"] = rsi(df["close"], p["rsi_period"])
        return df

    def _score_bar(self, row: pd.Series, p: dict) -> float:
        """Score a single bar 0-100 across 4 factors. The combined
        strategy is a custom function that does not currently fall
        under any known category (trend/momentum/volatility/volume)."""
        score = 0.0

        # 1. Trend: price above SMA(20) and SMA(50) = bullish
        if not pd.isna(row["sma_fast"]) and not pd.isna(row["sma_slow"]):
            trend_score = 0.0
            if row["close"] > row["sma_fast"]:
                trend_score += 40.0
            if row["close"] > row["sma_slow"]:
                trend_score += 40.0
            if row["sma_fast"] > row["sma_slow"]:
                trend_score += 20.0
            score += trend_score * p["trend_weight"]

        # 2. Momentum: recent returns
        if not pd.isna(row["mom_short"]) and not pd.isna(row["mom_long"]):
            mom_score = 0.0
            if row["mom_short"] > 0:
                mom_score += 40.0
            if row["mom_long"] > 0:
                mom_score += 40.0
            if row["mom_short"] > row["mom_long"]:
                mom_score += 20.0  # accelerating
            score += mom_score * p["momentum_weight"]

        # 3. Volatility: lower vol = higher score (we're long-biased)
        if not pd.isna(row["volatility"]) and row["volatility"] > 0:
            # Normalize: 1% daily vol → score 30, 3% → score 0
            vol_pct = row["volatility"] * 100
            vol_score = max(0.0, 100.0 - vol_pct * 30.0)
            score += vol_score * p["volatility_weight"]

        # 4. Volume: above-average volume confirms conviction
        if not pd.isna(row["volume_ratio"]) and row["volume_ratio"] > 0:
            vol_ratio = min(row["volume_ratio"], 3.0)  # cap at 3x
            vol_score = (vol_ratio / 3.0) * 100.0
            score += vol_score * p["volume_weight"]

        return score

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.meta.params
        scores = pd.Series(0.0, index=df.index, dtype="float64")

        for i in range(len(df)):
            scores.iloc[i] = self._score_bar(df.iloc[i], p)

        # Generate signals from scores
        signals = pd.Series("HOLD", index=df.index, dtype="object")
        in_position = False

        for i in range(len(df)):
            score = scores.iloc[i]
            rsi_val = df.iloc[i]["rsi"]

            if pd.isna(score):
                continue

            # Entry: composite > buy threshold and not overbought
            if not in_position and score >= p["buy_threshold"]:
                if pd.isna(rsi_val) or rsi_val < 70:
                    signals.iloc[i] = "BUY"
                    in_position = True

            # Exit: composite drops below sell threshold
            elif in_position and score < p["sell_threshold"]:
                signals.iloc[i] = "SELL"
                in_position = False

            # Emergency exit: RSI extremely overbought
            elif in_position and not pd.isna(rsi_val) and rsi_val > 85:
                signals.iloc[i] = "SELL"
                in_position = False

        return signals
