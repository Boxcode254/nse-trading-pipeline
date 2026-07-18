"""Top opportunities narrative section.

Slot for the Daily Investment Brief. Lists the strongest assets
today with a one-line reason each.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from ..narratives import risk as risk_mod, translator


def format_opportunities(
    entries: Iterable[dict[str, Any]],
    top_n: int = 3,
) -> str:
    """Return a narrative block for the top opportunities.

    Parameters
    ----------
    entries : iterable of ranking dicts
        Should already be sorted by score descending. The function
        re-sorts defensively in case the caller didn't.
    top_n : int
        Maximum number of assets to include. Defaults to 3 (the
        spec's "Top Opportunities" section size).
    """
    entries = list(entries) if entries else []
    if not entries:
        return "🌟  TOP OPPORTUNITIES\n\nNo assets to rank today."

    # Defensive sort
    entries = sorted(
        entries,
        key=lambda e: _safe_float(e.get("score")) or 0.0,
        reverse=True,
    )
    top = entries[:top_n]

    lines = ["🌟  TOP OPPORTUNITIES", ""]
    for i, entry in enumerate(top, start=1):
        sym = entry.get("symbol", "?")
        score = _safe_float(entry.get("score")) or 0.0
        tier = entry.get("recommendation", "Hold")
        score_payload = translator.translate_score(score)
        why = score_payload["why_it_matters"]

        # Add the top-2 factor highlights (no raw numbers)
        factors = entry.get("factors") or {}
        summary = translator.summarise_factors(factors)
        factor_note = ""
        if summary["strong"]:
            top_factors = ", ".join(name for name, _ in summary["strong"][:2])
            factor_note = f" Strengths: {top_factors}."

        lines.append(
            f"{i}. {sym} — {tier} (score {score:.0f}/100). {why}{factor_note}"
        )

    if len(entries) > top_n:
        lines.append("")
        lines.append(
            f"{len(entries) - top_n} more assets in the broader ranking — "
            f"see the full report for details."
        )

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
