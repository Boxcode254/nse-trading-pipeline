"""Portfolio allocation suggestions — STUB for Phase 3.

The spec defers real portfolio optimization (risk parity, full
rebalancing) to a later phase. This template surfaces a *simple*
core/satellite suggestion from the ranking engine so the daily
brief still has a portfolio section to point at.

Allocation rule (intentionally simple for now):
    - Take the top-ranked assets with score >= 50.
    - Weight each proportionally to its score, capped at 40%.
    - Anything left over is "stay in cash".
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


def format_portfolio(
    entries: Iterable[dict[str, Any]],
    max_weight_per_asset: float = 0.40,
) -> str:
    """Return a plain-language portfolio suggestion block.

    Parameters
    ----------
    entries : iterable of ranking dicts
        Typically the full ranking. We filter internally to score >= 50.
    max_weight_per_asset : float
        Cap so a single asset never exceeds this share. 0.40 is the
        spec's "house view" until real optimization lands.
    """
    entries = list(entries) if entries else []
    strong = [e for e in entries if (_safe_float(e.get("score")) or 0.0) >= 50.0]
    # Sort strongest first
    strong = sorted(strong, key=lambda e: _safe_float(e.get("score")) or 0.0,
                    reverse=True)

    lines = ["💼  PORTFOLIO SUGGESTION", ""]

    if not strong:
        lines.append(
            "No monitored assets score high enough to recommend right now. "
            "Stay in cash and review the market summary above."
        )
        return "\n".join(lines)

    # Proportional weighting, capped
    total = sum(_safe_float(e.get("score")) or 0.0 for e in strong)
    if total <= 0:
        total = 1.0

    weights: list[tuple[str, float, str]] = []
    for e in strong:
        sym = e.get("symbol", "?")
        score = _safe_float(e.get("score")) or 0.0
        tier = e.get("recommendation", "Hold")
        w = min(max_weight_per_asset, max_weight_per_asset * score / 100.0)
        weights.append((sym, w, tier))

    # Note that this is intentionally simple
    lines.append("Suggested core allocation (proportional to conviction, "
                 "capped at 40% per asset):")
    for sym, w, tier in weights:
        lines.append(f"  • {sym:<6s} — {w*100:>4.0f}%  ·  {tier}")

    deployed = sum(w for _, w, _ in weights)
    if deployed < 0.95:
        lines.append("")
        lines.append(
            f"Approx {deployed*100:.0f}% deployed · "
            f"{(1 - deployed)*100:.0f}% reserved as cash for the next setup."
        )
    else:
        lines.append("")
        lines.append("Approx 100% deployed — no cash buffer recommended at "
                     "current conviction levels.")

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
