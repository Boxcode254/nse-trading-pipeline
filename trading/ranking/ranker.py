"""Ranking engine — orchestrates scorer + tier mapping + reason generation.

Public surface
--------------
rank_assets(frames)         → list of per-asset ranking dicts (unsorted input)
build_ranking(frames)       → full ranking summary with metadata
recommendation_for(score)   → tier label from a numeric score
expected_holding_period(s)  → months string from a numeric score
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .. import config
from . import scorer
from .scorer import (
    SCORE_FACTOR_NAMES,
    aggregate_score,
    score_all_factors,
)


# ── Tier + holding period ────────────────────────────────────────────


def recommendation_for(score: float) -> str:
    """Map a 0-100 score to one of the 5 recommendation labels.

    Thresholds come from ``config.RECOMMENDATION_THRESHOLDS``. Higher
    score = more bullish recommendation. Ties go to the higher tier.
    """
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return config.TIER_HOLD
    score = float(score)
    # Walk the thresholds in order; first one whose lower bound is
    # satisfied wins. The list is ordered highest → lowest.
    last_label = config.TIER_AVOID
    for lower_bound, label in config.RECOMMENDATION_THRESHOLDS:
        if score >= lower_bound:
            return label
        last_label = label
    return last_label


def expected_holding_period(score: float) -> str:
    """Map a score to a holding-period estimate (6-24 months).

    Higher conviction → shorter holding period (we expect the move
    to play out faster). Lower conviction → longer wait, allowing
    the thesis to mature.
    """
    tier = recommendation_for(score)
    return config.HOLDING_PERIODS.get(tier, "18 months")


# ── Reason generation ────────────────────────────────────────────────


def _generate_reason(
    symbol: str,
    score: float,
    factors: dict[str, float],
    recommendation: str,
) -> str:
    """Plain-language 2-3 sentence explanation of the score.

    Picks the two strongest and weakest factor scores and weaves
    them into a sentence about why this asset got its tier. Pure
    template-based; no LLM calls.
    """
    # Identify top two and bottom two factors
    sorted_factors = sorted(factors.items(), key=lambda kv: kv[1], reverse=True)
    top = sorted_factors[:2]
    bottom = sorted_factors[-2:]

    def _fmt_pair(pair: tuple[str, float]) -> str:
        return f"{pair[0].replace('_', ' ')} ({pair[1]:.0f}/100)"

    top_str = " and ".join(_fmt_pair(p) for p in top)
    bottom_str = " and ".join(_fmt_pair(p) for p in bottom)

    sentiment = "strong" if score >= 75 else "moderate" if score >= 50 else "weak"
    horizon = expected_holding_period(score)

    sentences = [
        f"{symbol} scores {score:.0f}/100 — {sentiment} overall, "
        f"rating: {recommendation}.",
        f"Strengths: {top_str}.",
    ]
    if bottom[0][1] < 50:
        sentences.append(f"Weaknesses: {bottom_str}.")
    sentences.append(f"Expected horizon: {horizon}.")
    return " ".join(sentences)


# ── Ranking entry builder ────────────────────────────────────────────


def _build_entry(
    symbol: str,
    df: pd.DataFrame,
    universe: Optional[dict[str, pd.DataFrame]] = None,
    weights: Optional[dict[str, float]] = None,
) -> dict:
    """Build one ranking entry for ``symbol``.

    Computes all 8 factor scores, aggregates to a final score, maps
    to a recommendation, derives holding period, and generates the
    plain-language reason.
    """
    factors = score_all_factors(df, universe=universe)
    score = aggregate_score(factors, weights=weights)
    recommendation = recommendation_for(score)
    return {
        "symbol": symbol,
        "score": round(float(score), 2),
        "recommendation": recommendation,
        "factors": {k: round(float(v), 2) for k, v in factors.items()},
        "reason": _generate_reason(symbol, score, factors, recommendation),
        "holding_period": expected_holding_period(score),
    }


# ── Public ranking functions ─────────────────────────────────────────


def rank_assets(
    frames: dict[str, pd.DataFrame],
    weights: Optional[dict[str, float]] = None,
) -> list[dict]:
    """Rank a dict of {symbol: OHLCV DataFrame} by aggregate score.

    Returns a list of ranking entries sorted by score descending.
    Each entry is a dict with the full per-asset breakdown.
    """
    # All frames are the universe (for relative strength)
    universe = frames

    entries: list[dict] = []
    for symbol, df in frames.items():
        try:
            entry = _build_entry(symbol, df, universe=universe, weights=weights)
        except Exception as exc:  # noqa: BLE001
            # One bad asset must not kill the whole ranking
            entry = {
                "symbol": symbol,
                "score": 0.0,
                "recommendation": config.TIER_AVOID,
                "factors": {k: 0.0 for k in SCORE_FACTOR_NAMES},
                "reason": f"Could not score {symbol}: {type(exc).__name__}: {exc}",
                "holding_period": "24 months",
            }
        entries.append(entry)

    entries.sort(key=lambda e: e["score"], reverse=True)
    # Add rank position (1-based)
    for i, entry in enumerate(entries, start=1):
        entry["rank"] = i
    return entries


def build_ranking(
    frames: dict[str, pd.DataFrame],
    weights: Optional[dict[str, float]] = None,
) -> dict:
    """Build a complete ranking summary (used by the CLI + report).

    Returns a dict with keys: ``ranked`` (list of entries, sorted),
    ``weights`` (the weights actually used), ``top_n`` (a convenience
    list of the top 3 by score).
    """
    ranked = rank_assets(frames, weights=weights)
    actual_weights = weights or dict(config.SCORING_WEIGHTS)
    return {
        "ranked": ranked,
        "weights": actual_weights,
        "top_n": ranked[:3],
    }
