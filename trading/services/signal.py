"""Signal service.

Generates per-asset signal dicts combining the signal engine, the
ranking engine, and the signal validator. Returns plain dicts so the
CLI / dashboard / REST can format freely.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from .. import config
from ..ranking import ranker, scorer
from ..signals import engine as signal_engine
from ..signals import validator as signal_validator
from . import market


def recommendation_for(score: float) -> str:
    """Map a 0-100 score to one of the 5 recommendation labels."""
    return ranker.recommendation_for(score)


def _latest_signals_for_pair(pair: str) -> list[dict[str, Any]]:
    """Compute the per-bar signal series for a single pair."""
    df = market.fetch_one(pair)
    if df is None or df.empty:
        return []
    return signal_engine.generate_signals(df, pair=pair)


def signal_for_symbol(
    symbol: str,
    pair: Optional[str] = None,
) -> dict[str, Any]:
    """Return a single recommendation dict for one symbol.

    Combines the latest bar signal with the ranking engine's score
    and tier. Falls back gracefully if any step fails.

    Output schema::

        {
          "symbol": "SCOM",
          "score": 82.5,
          "recommendation": "Accumulate",
          "confidence": 0.62,
          "explanation": "Plain English reasoning ...",
          "indicators": {
             "price": 23.45, "rsi": 55.2, "sma_fast": 23.1,
             "sma_slow": 22.8, "trend": "up"
          },
          "source": "yfinance"
        }
    """
    pair = pair or symbol
    df = market.fetch_one(pair)
    if df is None or df.empty:
        return {"symbol": symbol, "score": 0.0, "recommendation": config.TIER_AVOID,
                "confidence": 0.0, "explanation": "No data available.", "indicators": {}}

    # Latest raw signal
    series = signal_engine.generate_signals(df, pair=pair)
    last = series[-1] if series else {}
    accepted, _ = signal_validator.filter_signals(series, df)
    validated = accepted[-1] if accepted else last

    # Ranking score (8-factor)
    factors = scorer.score_all_factors(df, universe={pair: df})
    score = float(scorer.aggregate_score(factors))
    recommendation = ranker.recommendation_for(score)
    confidence = float(validated.get("confidence", 0.0)) if validated else 0.0

    return {
        "symbol": symbol,
        "score": round(score, 2),
        "recommendation": recommendation,
        "confidence": round(confidence, 4),
        "explanation": "",  # populated by explain_symbol()
        "indicators": {
            "price": float(validated.get("price")) if validated and validated.get("price") is not None else None,
            "rsi": float(validated["rsi"]) if validated and validated.get("rsi") is not None and not pd.isna(validated.get("rsi", float("nan"))) else None,
            "sma_fast": float(validated["sma_fast"]) if validated and validated.get("sma_fast") is not None and not pd.isna(validated.get("sma_fast", float("nan"))) else None,
            "sma_slow": float(validated["sma_slow"]) if validated and validated.get("sma_slow") is not None and not pd.isna(validated.get("sma_slow", float("nan"))) else None,
            "signal": validated.get("signal", "HOLD") if validated else "HOLD",
        },
        "source": df.attrs.get("source", "?"),
    }


def explain_symbol(symbol: str, pair: Optional[str] = None) -> dict[str, Any]:
    """Return a plain-English explanation for one symbol.

    Output schema::

        {
          "symbol": "SCOM",
          "score": 82.5,
          "recommendation": "Accumulate",
          "explanation": "Momentum is beginning to improve, but ..."
        }
    """
    out = signal_for_symbol(symbol, pair=pair)
    score = out["score"]
    recommendation = out["recommendation"]
    ind = out["indicators"]

    rsi = ind.get("rsi")
    signal_type = ind.get("signal", "HOLD")
    price = ind.get("price")

    # Plain-English narrative. No raw indicator dumps.
    if recommendation == config.TIER_STRONG_ACCUMULATE:
        lead = f"{symbol} is currently a strong opportunity."
    elif recommendation == config.TIER_ACCUMULATE:
        lead = f"{symbol} is showing attractive conditions for accumulation."
    elif recommendation == config.TIER_HOLD:
        lead = f"{symbol} is in a neutral phase — no clear edge either way."
    elif recommendation == config.TIER_REDUCE:
        lead = f"{symbol} is showing weakness; reducing exposure may be wise."
    else:
        lead = f"{symbol} is currently unattractive on most factors."

    if rsi is None:
        momentum = "Momentum indicators are not yet available — not enough data."
    elif rsi >= 70:
        momentum = "Buyers are aggressive, though the move may be overextended."
    elif rsi >= 55:
        momentum = "Momentum is positive and improving."
    elif rsi >= 45:
        momentum = "Momentum is balanced — neither buyers nor sellers are in control."
    elif rsi >= 30:
        momentum = "Momentum is beginning to improve, but buyers have not yet demonstrated sustained strength."
    else:
        momentum = "Sellers are firmly in control and conditions are washed out."

    trend_note = ""
    if signal_type == "BUY":
        trend_note = " A fresh bullish crossover has just been registered."
    elif signal_type == "SELL":
        trend_note = " A bearish crossover has just been registered."

    explanation = f"{lead} {momentum}{trend_note}"
    out["explanation"] = explanation
    return out
