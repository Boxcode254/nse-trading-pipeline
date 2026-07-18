"""Sector rotation tracker.

Computes per-sector performance (last 30 days) and classifies the
rotation direction (in / out / neutral). For a small universe of
six NSE tickers, "sector" is a coarse but useful grouping: when
all banks are rallying together, that's a sector move worth
mentioning in the daily brief.

Data source
-----------

Default: the engine's own per-symbol score history. We don't have
price history, but the ranking service tracks a ``score`` per
symbol; the change in average sector score is a reasonable proxy
for rotation.

The data flow::

    services/ranking.build()        # ranked list with score
        ↓
    services.market_intel.sector._compute_sector_perf
        ↓
    list of {sector, perf_pct, rotation}
"""
from __future__ import annotations

from typing import Optional


# The canonical symbol → sector map. Lives here as a single source
# of truth; the calendar module mirrors it for `_sector_for_symbol`
# to keep the two modules independently usable.
_SECTOR_MAP: dict[str, str] = {
    "SCOM": "telecom",
    "KCB": "banking",
    "EQTY": "banking",
    "ABSA": "banking",
    "SCBK": "banking",
    "EABL": "consumer",
    "EUR/USD": "forex",
    "USD/KES": "forex",
}


def sector_for(symbol: str) -> str:
    """Return the sector for a symbol, or ``"other"`` if unknown."""
    return _SECTOR_MAP.get(symbol.upper(), "other")


def snapshot(*, lookback_days: int = 30) -> list[dict]:
    """Return one summary dict per sector.

    Each dict has shape::

        {
            "sector":   "banking",
            "perf_pct": 2.1,   # change in avg score over lookback
            "rotation": "in" | "out" | "neutral",
            "members":  ["KCB", "EQTY", ...],
        }
    """
    return _compute_sector_perf(lookback_days=lookback_days)


def _classify_rotation(perf_pct: float) -> str:
    """Classify a percentage change as a rotation direction."""
    if perf_pct >= 1.0:
        return "in"
    if perf_pct <= -1.0:
        return "out"
    return "neutral"


def format_line(entry: dict) -> str:
    """Format a single sector entry as a one-liner."""
    sector = entry.get("sector", "?")
    perf = entry.get("perf_pct", 0.0)
    rotation = entry.get("rotation", "neutral")
    sign = "+" if perf >= 0 else ""
    return f"{sector}: {sign}{perf:.1f}% ({rotation})"


# ── Source backend (overridable) ──────────────────────────────────


def _compute_sector_perf(*, lookback_days: int = 30) -> list[dict]:
    """Compute sector performance from the ranking service.

    The default implementation returns an empty list — the
    integration with ``ranking`` is wired in ``scanner.py`` /
    callers, not here, to keep this module independently testable.

    Tests patch this function to inject fixtures.
    """
    return []
