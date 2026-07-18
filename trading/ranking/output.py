"""Plain-language formatter for ranking output.

Produces three distinct text artifacts:

- format_ranking_summary(ranked)  — full ranked table with scores + tiers
- format_top_opportunities(ranked, top_n) — "Top Opportunities" block
- format_factor_breakdown(factors) — per-factor bar chart for one asset
"""
from __future__ import annotations

from typing import Sequence


_EMOJI = {
    "Strong Accumulate": "🟢",
    "Accumulate": "🟩",
    "Hold": "🟡",
    "Reduce": "🟠",
    "Avoid": "🔴",
}

# Visual bar for a 0-100 score (width 12)
_BAR_FULL = "█"
_BAR_EMPTY = "░"
_BAR_WIDTH = 12


def _bar(score: float, width: int = _BAR_WIDTH) -> str:
    """Visual progress bar for a 0-100 score."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0.0
    filled = max(0, min(width, int(round(s / 100.0 * width))))
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def _emoji_for(tier: str) -> str:
    return _EMOJI.get(tier, "⚪")


def _recommendation_phrase(rec: str) -> str:
    """Map a tier label to a short action phrase for the summary."""
    return {
        "Strong Accumulate": "Strong buy",
        "Accumulate": "Buy",
        "Hold": "Hold",
        "Reduce": "Trim",
        "Avoid": "Avoid",
    }.get(rec, rec)


def format_ranking_summary(ranked: Sequence[dict]) -> str:
    """Format the full ranked list as a human-readable table.

    Output layout::

        🏆  MARKET RANKING — Top opportunities across all tracked assets
        ════════════════════════════════════════════════════════════

        #1  SCOM    87  ████████████  Strong Accumulate  (6 months)
              KCB    74  █████████░░░  Accumulate         (12 months)
        ...

    Each row shows rank, symbol, score, visual bar, tier, and
    expected holding period. Sections are wrapped in horizontal
    rules for readability.
    """
    if not ranked:
        return "🏆  MARKET RANKING\n\nNo assets to rank."

    lines = [
        "🏆  MARKET RANKING — Top opportunities across all tracked assets",
        "═" * 70,
        "",
    ]

    for entry in ranked:
        rank = entry.get("rank", 0)
        sym = entry.get("symbol", "?")
        score = entry.get("score", 0.0)
        tier = entry.get("recommendation", "Hold")
        holding = entry.get("holding_period", "18 months")
        emoji = _emoji_for(tier)
        bar = _bar(score)
        lines.append(
            f"#{rank:<2d}  {sym:<8s}  {score:5.1f}  {bar}  "
            f"{emoji} {tier:<18s}  ({holding})"
        )
        # Brief reason underneath
        reason = entry.get("reason", "")
        if reason:
            # One-line summary: just the first sentence
            first = reason.split(".")[0] + "."
            lines.append(f"      {first}")

    lines.append("")
    lines.append("═" * 70)
    return "\n".join(lines)


def format_top_opportunities(ranked: Sequence[dict], top_n: int = 3) -> str:
    """Format the top N opportunities as a compact block.

    Designed to slot into the daily report's "Top Opportunities"
    section — character-budget aware.
    """
    if not ranked:
        return "🌟  TOP OPPORTUNITIES\n\nNo assets to rank."

    top = ranked[:top_n]
    lines = ["🌟  TOP OPPORTUNITIES", ""]
    for i, entry in enumerate(top, start=1):
        sym = entry.get("symbol", "?")
        score = entry.get("score", 0.0)
        tier = entry.get("recommendation", "Hold")
        holding = entry.get("holding_period", "18 months")
        action = _recommendation_phrase(tier)
        emoji = _emoji_for(tier)
        lines.append(
            f"{i}. {sym}  {score:.0f}  {emoji} {tier}  ·  {action}  ({holding})"
        )
    return "\n".join(lines)


def format_factor_breakdown(factors: dict[str, float]) -> str:
    """Render a per-factor bar chart for a single asset.

    Used in the detailed ranking report so the user can see WHY
    an asset got its score, not just WHAT the score was.
    """
    if not factors:
        return ""

    lines = ["Factor scores:"]
    for name, score in factors.items():
        label = name.replace("_", " ").capitalize()
        bar = _bar(score)
        lines.append(f"  {label:<22s}  {score:5.1f}  {bar}")
    return "\n".join(lines)


def format_full_ranking_report(
    ranked: Sequence[dict],
    weights: dict[str, float],
) -> str:
    """Compose the full ranking report (summary + per-asset detail).

    Used by the ``rank`` CLI command. Length is bounded to fit a
    Telegram message; the per-asset detail section is only included
    for the top 5 to stay under the limit.
    """
    if not ranked:
        return "🏆  MARKET RANKING\n\nNo assets to rank."

    parts = [format_ranking_summary(ranked), ""]
    parts.append("⚖️  Scoring weights")
    parts.append("─" * 40)
    for name, w in weights.items():
        label = name.replace("_", " ").capitalize()
        parts.append(f"  {label:<22s}  {w * 100:5.1f}%")
    parts.append("")

    # Per-asset detail for the top 5
    parts.append("📋  TOP 5 — FACTOR DETAIL")
    parts.append("─" * 40)
    for entry in ranked[:5]:
        sym = entry.get("symbol", "?")
        parts.append(f"\n{sym} — score {entry.get('score', 0):.1f} "
                     f"({entry.get('recommendation', '')})")
        parts.append(format_factor_breakdown(entry.get("factors", {})))
        reason = entry.get("reason", "")
        if reason:
            parts.append("")
            parts.append(reason)

    return "\n".join(parts)
