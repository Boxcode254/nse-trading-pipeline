"""Context assembler — picks the top-N most relevant items for a symbol.

Given a symbol, this function:

1. Fetches news headlines mentioning that symbol.
2. Fetches macro events that affect the symbol's sector.
3. Looks up the next earnings report date.
4. Looks up the current sector rotation.
5. Deduplicates across sources (e.g. a CBK rate decision is one
   item, not five items with similar wording).
6. Sorts by relevance and returns the top ``max_items`` items.

The output is a list of dicts with a uniform shape so the advisor
can render them without caring which module produced them::

    {
        "kind":      "news" | "calendar" | "earnings" | "sector",
        "label":     "positive" | "negative" | "neutral" | "macro" | "upcoming",
        "text":      "Safaricom announced 5G expansion",
        "timestamp": "2026-06-28T10:00:00Z",
        "relevance": 0.9,
        "source":    "alpha" | "finviz" | "google" | "calendar" | "earnings" | "sector",
    }
"""
from __future__ import annotations

from typing import Optional

from . import calendar, earnings, news, sector
from . import sentiment as sentiment_mod


# ── Public API ────────────────────────────────────────────────────


def assemble(symbol: str, *, max_items: int = 3) -> list[dict]:
    """Return the top-``max_items`` context items for ``symbol``.

    Returns ``[]`` if no context is available. Never raises — all
    upstream failures are swallowed so the advisor always has a
    safe default.
    """
    symbol = symbol.upper()
    items: list[dict] = []

    # ── News ──────────────────────────────────────────────────────
    try:
        news_items = news.fetch([symbol])
    except Exception:  # noqa: BLE001
        news_items = []
    for n in news_items[:max_items * 2]:  # grab a few extra, we'll dedupe
        items.append({
            "kind": "news",
            "label": sentiment_mod.classify(n.get("sentiment", 0.0)),
            "text": n.get("headline", ""),
            "timestamp": n.get("timestamp", ""),
            "relevance": float(n.get("relevance", 0.0)),
            "source": n.get("source", "news"),
        })

    # ── Calendar ──────────────────────────────────────────────────
    try:
        cal_events = calendar.upcoming()
    except Exception:  # noqa: BLE001
        cal_events = []
    relevant = _filter_relevant(cal_events, symbol)
    # If we have a sector rotation signal, also include the symbol's
    # sector as a calendar-relevant event when the sector is moving.
    for ev in relevant[:max_items]:
        items.append({
            "kind": "calendar",
            "label": "macro",
            "text": ev.get("event", ""),
            "timestamp": ev.get("date", ""),
            "relevance": 0.6 if ev.get("impact") == "high" else 0.4,
            "source": "calendar",
        })

    # ── Earnings ──────────────────────────────────────────────────
    try:
        earn = earnings.upcoming([symbol])
    except Exception:  # noqa: BLE001
        earn = {}
    entry = earn.get(symbol)
    if entry and entry.get("status") != "reported":
        label = "earnings"
        if entry.get("status") == "pre-earnings":
            label = "pre-earnings"
        elif entry.get("status") == "upcoming":
            label = "upcoming"
        impact_bonus = 0.0
        if entry.get("expected_impact") == "high":
            impact_bonus = 0.2
        items.append({
            "kind": "earnings",
            "label": label,
            "text": f"{symbol} reports {entry.get('report_date', '?')}",
            "timestamp": entry.get("report_date", ""),
            "relevance": 0.5 + impact_bonus,
            "source": "earnings",
        })

    # ── Sector rotation ───────────────────────────────────────────
    try:
        snap = sector.snapshot()
    except Exception:  # noqa: BLE001
        snap = []
    sym_sector = sector.sector_for(symbol)
    for s in snap:
        if s.get("sector") == sym_sector and s.get("rotation") != "neutral":
            sign = "+" if s.get("perf_pct", 0) >= 0 else ""
            items.append({
                "kind": "sector",
                "label": "positive" if s.get("rotation") == "in" else "negative",
                "text": (
                    f"{sym_sector} sector {s.get('rotation')} "
                    f"({sign}{s.get('perf_pct', 0):.1f}%)"
                ),
                "timestamp": "",
                "relevance": 0.4,
                "source": "sector",
            })

    # ── Dedupe by text similarity ─────────────────────────────────
    items = _dedupe(items)

    # ── Sort + trim ───────────────────────────────────────────────
    items.sort(key=lambda i: (i.get("relevance", 0.0), i.get("timestamp", "")),
               reverse=True)
    return items[:max_items]


def format_block(symbol: str, items: list[dict]) -> str:
    """Render an assembled context list as a human-readable block.

    Used by the advisor to print a "Market Context for SCOM"
    section. Returns a short string — never raises.
    """
    sym = symbol.upper()
    if not items:
        return f"Market context for {sym}: no notable drivers right now."
    lines = [f"Market context for {sym}:"]
    for it in items:
        label = it.get("label", "")
        text = it.get("text", "")
        ts = it.get("timestamp", "")
        prefix = f"[{label}]" if label else ""
        ts_part = f"  ({ts})" if ts else ""
        lines.append(f"  • {prefix} {text}{ts_part}".strip())
    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────


def _filter_relevant(events: list[dict], symbol: str) -> list[dict]:
    """Return only events that affect ``symbol`` (by ticker or sector).

    Mirrors ``calendar._filter_relevant`` so this module is decoupled
    from calendar's internals. Kept in sync; if the calendar's
    sector map grows, update this too.
    """
    sym = symbol.upper()
    sym_sector = sector.sector_for(sym)
    out = []
    for ev in events:
        if sym in (ev.get("tickers") or []):
            out.append(ev)
            continue
        if sym_sector and sym_sector in (ev.get("sectors") or []):
            out.append(ev)
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    """Drop near-duplicate items (same text after lowercasing + trimming).

    We do a simple pairwise comparison: two items are duplicates if
    their ``text`` is the same after lowercasing and stripping
    punctuation, *or* if one is a substring of the other (catches
    "CBK rate decision" vs "CBK rate decision — analyst call").
    """
    seen: list[str] = []
    out: list[dict] = []
    for it in items:
        t = (it.get("text") or "").lower().strip()
        t = _normalize(t)
        if not t:
            continue
        is_dup = False
        for s in seen:
            if t == s or t in s or s in t:
                is_dup = True
                break
        if not is_dup:
            seen.append(t)
            out.append(it)
    return out


def _normalize(t: str) -> str:
    """Strip common noise from headline text for dedupe."""
    for ch in ".,;:!?":
        t = t.replace(ch, " ")
    return " ".join(t.split())
