"""Ranking service.

Thin facade over the existing ranking engine. Returns a complete
ranking summary plus convenience helpers (top N, score for a symbol).
"""
from __future__ import annotations

from typing import Any, Optional

from ..ranking import ranker
from . import market


def build(frames: Optional[dict] = None) -> dict[str, Any]:
    """Build a full ranking summary for the configured universe.

    If *frames* is None, fetches data for every configured pair.
    Output schema::

        {
          "ranked": [ {rank, symbol, score, recommendation, ...}, ... ],
          "weights": {factor: weight, ...},
          "top_n": [first 3 entries],
        }
    """
    if frames is None:
        frames = market.fetch_all()
    if not frames:
        return {"ranked": [], "weights": {}, "top_n": []}
    return ranker.build_ranking(frames)


def top(n: int = 3) -> list[dict[str, Any]]:
    """Return the top *n* ranked assets. Convenience wrapper."""
    return build().get("top_n", [])[:n]


def score_for(symbol: str) -> Optional[dict[str, Any]]:
    """Return the ranking entry for *symbol*, or None if not present."""
    result = build()
    for entry in result.get("ranked", []):
        if entry.get("symbol") == symbol:
            return entry
    return None
