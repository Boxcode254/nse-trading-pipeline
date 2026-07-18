"""Macro-economic event calendar.

Tracks upcoming central bank decisions, inflation prints, GDP
releases, and sector-relevant events. Each event is tagged with
the sectors and tickers it might affect, so the context assembler
can answer "is there a CBK decision this week that will move KCB?"

Data source
-----------

Primary: Investing.com (HTML scrape — free, no auth) and TradingView
(tvdatafeed — already integrated in the rest of the project).

For the offline default, the module ships a small **static seed**
of recurring events the project already knows about:

- CBK rate decision (every 2 months)
- US Fed FOMC (8 times per year)
- Kenya CPI release (monthly)
- Kenya GDP release (quarterly)

These are stubbed with placeholder dates relative to "now" so the
advisor always has *something* to mention. When a real source is
added (the hook is ``_fetch_events``), it takes priority.

Each event shape::

    {
        "event":        str,   # "CBK rate decision"
        "date":         str,   # ISO 8601 date "YYYY-MM-DD"
        "impact":       str,   # "high" | "medium" | "low"
        "sectors":      list,  # ["banking", "forex"]
        "tickers":      list,  # ["KCB", "EQTY"]
        "country":      str,   # "KE" | "US" | ...
    }
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional


def upcoming(
    *,
    within_days: int = 30,
    use_cache: bool = True,
) -> list[dict]:
    """Return macro events in the next ``within_days`` days.

    The window filter is applied to *both* live and seeded events so
    callers always get a forward-looking list. Pass
    ``within_days=365*100`` if you want every known event.
    """
    events = _fetch_events()
    if not events:
        events = _static_seed()
    return _filter_window(events, within_days=within_days)


def _filter_relevant(events: list[dict], symbol: str) -> list[dict]:
    """Return only events that affect ``symbol`` (by ticker or sector)."""
    sym = symbol.upper()
    sym_sector = _sector_for_symbol(sym)
    out = []
    for ev in events:
        if sym in (ev.get("tickers") or []):
            out.append(ev)
            continue
        if sym_sector and sym_sector in (ev.get("sectors") or []):
            out.append(ev)
    return out


def _sector_for_symbol(symbol: str) -> Optional[str]:
    """Tiny inline sector map; mirrors the one in sector.py."""
    mapping = {
        "SCOM": "telecom",
        "KCB": "banking",
        "EQTY": "banking",
        "ABSA": "banking",
        "SCBK": "banking",
        "EABL": "consumer",
    }
    return mapping.get(symbol)


def format_event(ev: dict) -> str:
    """Format a single event as a one-liner."""
    parts = [
        f"{ev.get('event', '?')} on {ev.get('date', '?')}",
    ]
    impact = ev.get("impact")
    if impact:
        parts.append(f"({impact} impact)")
    sectors = ev.get("sectors") or []
    if sectors:
        parts.append(f"sectors: {', '.join(sectors)}")
    tickers = ev.get("tickers") or []
    if tickers:
        parts.append(f"tickers: {', '.join(tickers)}")
    return "  ".join(parts)


# ── Source backend (overridable in tests) ──────────────────────────


def _fetch_events() -> list[dict]:
    """Return events from the live source, or [] if unavailable.

    The default implementation returns [] (offline). Tests and
    future deployments can monkey-patch this function to return
    real events.
    """
    return []


# ── Static seed ────────────────────────────────────────────────────


def _static_seed() -> list[dict]:
    """A small set of recurring macro events the project knows about.

    Dates are offset from today so the events are always "in the
    near future" relative to the run.
    """
    today = datetime.now(timezone.utc).date()
    out = [
        # Next CBK MPC meeting — bi-monthly cadence
        {
            "event": "CBK rate decision",
            "date": (today + timedelta(days=21)).isoformat(),
            "impact": "high",
            "sectors": ["banking", "forex"],
            "tickers": ["KCB", "EQTY", "ABSA", "SCBK"],
            "country": "KE",
        },
        # US Fed FOMC (next quarterly meeting — every ~6 weeks)
        {
            "event": "US Fed FOMC decision",
            "date": (today + timedelta(days=10)).isoformat(),
            "impact": "high",
            "sectors": ["forex", "banking", "all"],
            "tickers": [],
            "country": "US",
        },
        # Kenya CPI release (monthly)
        {
            "event": "Kenya CPI release",
            "date": (today + timedelta(days=5)).isoformat(),
            "impact": "medium",
            "sectors": ["all"],
            "tickers": [],
            "country": "KE",
        },
        # Kenya GDP (quarterly)
        {
            "event": "Kenya GDP release",
            "date": (today + timedelta(days=45)).isoformat(),
            "impact": "medium",
            "sectors": ["all"],
            "tickers": [],
            "country": "KE",
        },
    ]
    return out


def _filter_window(events: Iterable[dict], *, within_days: int) -> list[dict]:
    """Keep only events within ``within_days`` of today (forward only)."""
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=within_days)
    out = []
    for ev in events:
        try:
            d = date.fromisoformat(str(ev.get("date", "")))
        except (TypeError, ValueError):
            continue
        if today <= d <= horizon:
            out.append(ev)
    out.sort(key=lambda e: e.get("date", ""))
    return out
