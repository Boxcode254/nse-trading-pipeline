"""Assets to avoid — narrative section."""
from __future__ import annotations

from typing import Any, Iterable, Optional


def format_warnings(
    entries: Iterable[dict[str, Any]],
    top_n: int = 3,
) -> str:
    """Return a narrative block for the worst-scoring assets.

    Sorts *ascending* by score (lowest = worst). Empty input is
    handled gracefully (a clean report is a good report).
    """
    entries = list(entries) if entries else []
    if not entries:
        return "⚠️  ASSETS TO AVOID\n\nNo assets flagged to avoid today."

    # Sort worst → best so the most-damaged appear first
    entries = sorted(
        entries,
        key=lambda e: _safe_float(e.get("score")) or 0.0,
    )
    worst = entries[:top_n]

    lines = ["⚠️  ASSETS TO AVOID", ""]
    for i, entry in enumerate(worst, start=1):
        sym = entry.get("symbol", "?")
        score = _safe_float(entry.get("score")) or 0.0
        tier = entry.get("recommendation", "Avoid")
        if score < 25:
            reason = "Multiple risk factors are present with limited upside potential."
        elif score < 50:
            reason = "Risk factors are beginning to outweigh the positives."
        else:
            reason = "Not the strongest setup in the current environment."

        lines.append(f"{i}. {sym} — {tier} (score {score:.0f}/100). {reason}")

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
