"""Generate the broader market context narrative.

Looks at the full ranked universe and produces a single paragraph
describing the overall environment — how many assets are bullish,
how cautious the average signal is, and what that means for a
beginner thinking about putting money to work today.
"""
from __future__ import annotations

from typing import Any, Iterable


def summarise_market_context(
    ranked: Iterable[dict[str, Any]],
) -> str:
    """Return a single-sentence (or two-sentence) market context line.

    The output is intentionally short — it slots into the top of the
    Daily Investment Brief before the ranked list.
    """
    ranked = list(ranked) if ranked else []
    if not ranked:
        return (
            "No monitored assets are producing reliable readings right now. "
            "Stay in cash and check back once the data stabilises."
        )

    # Bucket counts — score-based so the language stays consistent
    # regardless of how tier labels evolve. >= 67 = bull, <= 33 = bear,
    # in between = neutral.
    n = len(ranked)
    bull = sum(1 for r in ranked if _safe_float(r.get("score")) is not None
               and _safe_float(r.get("score")) >= 67.0)
    avoid = sum(1 for r in ranked if _safe_float(r.get("score")) is not None
                and _safe_float(r.get("score")) <= 33.0)
    hold = n - bull - avoid

    avg_score = sum(_safe_float(r.get("score")) or 0.0 for r in ranked) / n

    # Build a context line based on the balance
    if bull >= max(2, n // 2):
        return (
            f"Overall market conditions are favourable for risk-taking. "
            f"{bull} of {n} tracked assets are in bullish regimes, "
            f"with an average score of {avg_score:.0f}/100."
        )
    if avoid >= max(2, n // 2) or avg_score < 35:
        return (
            f"Caution is warranted. {avoid} of {n} tracked assets are in "
            f"bearish or avoid regimes and the average conviction score "
            f"is only {avg_score:.0f}/100. Defensive positioning is "
            f"the right call until conditions improve."
        )
    if hold >= max(2, n // 2) or (hold + avoid) >= n // 2:
        return (
            f"The market is mixed — {bull} bullish, {avoid} bearish, "
            f"and the rest neutral with an average score of "
            f"{avg_score:.0f}/100. Selective positioning beats broad "
            f"exposure in this environment."
        )
    # Mixed but no clear majority
    return (
        f"Conditions are mixed across the {n} tracked assets — "
        f"{bull} bullish, {hold} neutral, {avoid} "
        f"avoid. Treat each opportunity on its own merits rather than "
        f"basing the decision on the overall mood."
    )


def _safe_float(x: Any):
    """Best-effort float coercion; None for NaN / non-numeric."""
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
