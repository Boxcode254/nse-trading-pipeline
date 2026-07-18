"""Market summary template — used in the Daily Investment Brief.

Combines the market opportunity score (avg of all asset scores),
the regime distribution, and a context line into a short block.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


def format_summary(
    market_score: float,
    ranked: Iterable[dict[str, Any]],
    context_line: str = "",
) -> str:
    """Return a short market summary block.

    Parameters
    ----------
    market_score : float
        The 0-100 opportunity score (mean of all asset scores).
    ranked : iterable
        The full ranked list, used to derive the regime distribution.
    context_line : str, optional
        A pre-computed context sentence. If empty, we generate one
        from the ranked list using ``market_context.summarise_market_context``.
    """
    ranked = list(ranked) if ranked else []
    score = _safe_float(market_score) or 0.0

    if not context_line:
        from ..narratives import market_context
        context_line = market_context.summarise_market_context(ranked)

    # Regime distribution — count how many are bull / bear / neutral
    bull = bear = side = 0
    for r in ranked:
        score_v = _safe_float(r.get("score")) or 0.0
        if score_v >= 67:
            bull += 1
        elif score_v <= 33:
            bear += 1
        else:
            side += 1

    lines = [
        "📊  MARKET SUMMARY",
        f"  Opportunity score: {score:.0f}/100",
        f"  Bullish: {bull}  ·  Neutral: {side}  ·  Bearish: {bear}",
        "",
        context_line,
    ]
    return "\n".join(lines)


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    import math
    if math.isnan(v):
        return None
    return v
