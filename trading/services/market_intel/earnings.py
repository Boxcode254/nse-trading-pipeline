"""Earnings calendar tracker for NSE-listed stocks.

Reports for the six NSE tickers we track (SCOM, KCB, EQTY, EABL,
ABSA, SCBK) on a quarterly cadence. Most listed Kenyan companies
report within a 6-week window at the end of each quarter:

- Q1 results: April–May
- Q2 results (half-year): July–August
- Q3 results: October–November
- Full-year: February–March

The exact dates are not published far in advance, so this module
returns a coarse "expected window" and a status flag:

- ``"upcoming"``    — report > 14 days away
- ``"pre-earnings"``— report within 14 days (volatility expected)
- ``"reported"``    — report already in the past
- ``"unknown"``     — no data available

Data source
-----------

Primary: NSE announcements page (HTML scrape) or a paid data feed
(when wired up). The default returns a coarse estimate based on
calendar quarters. Tests can patch ``_fetch_earnings``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional


# Cadence offsets (in days) relative to a quarter's end month.
# A report is expected to land somewhere in this window after the
# quarter ends. We pick the *middle* of the window for the estimate.
_QUARTER_END_MONTHS = {
    "Q1": (3, 15, 60),    # March, mid-April to mid-May
    "Q2": (6, 30, 60),    # June end, July–August
    "Q3": (9, 30, 60),    # Sept end, Oct–Nov
    "Q4": (12, 15, 60),   # Dec, Jan–Feb
}


def upcoming(symbols: Iterable[str]) -> dict[str, dict]:
    """Return ``{symbol: earnings_info}`` for each requested ticker.

    Each info dict has shape::

        {
            "report_date":    "YYYY-MM-DD",
            "status":         "upcoming" | "pre-earnings" | "reported" | "unknown",
            "expected_impact": "low" | "medium" | "high",
        }
    """
    symbols = list(symbols) if symbols else []
    if not symbols:
        return {}

    fetched = _fetch_earnings(symbols) or {}
    today = datetime.now(timezone.utc).date()

    out: dict[str, dict] = {}
    for sym in symbols:
        sym_upper = sym.upper()
        info = fetched.get(sym_upper)
        if info is None:
            info = _estimate_window(sym_upper, today)
        # If we got a date from the live source, classify it.
        if "report_date" in info and info.get("status") is None:
            info["status"] = _classify_window(
                info["report_date"], today.isoformat()
            )
        if "expected_impact" not in info:
            info["expected_impact"] = "medium"
        out[sym_upper] = info
    return out


def _classify_window(report_date: str, today: str) -> str:
    """Classify the report date relative to today.

    - "reported"     if report_date is in the past
    - "pre-earnings" if within 14 days
    - "upcoming"     otherwise
    """
    try:
        rd = datetime.fromisoformat(str(report_date)).date()
        td = datetime.fromisoformat(str(today)).date()
    except (TypeError, ValueError):
        return "unknown"
    if rd < td:
        return "reported"
    if rd <= td + timedelta(days=14):
        return "pre-earnings"
    return "upcoming"


def format_line(symbol: str, info: dict) -> str:
    """Format a single earnings entry as a one-liner."""
    date = info.get("report_date", "?")
    status = info.get("status", "unknown")
    impact = info.get("expected_impact", "medium")
    return f"{symbol}: reports {date} [{status}, {impact} impact]"


# ── Source backend (overridable) ──────────────────────────────────


def _fetch_earnings(symbols: list[str]) -> dict[str, dict]:
    """Return ``{symbol: {report_date, expected_impact}}`` from the
    live source, or {} if unavailable.

    The default returns {}. Tests can patch this to inject fixtures.
    """
    return {}


# ── Estimation helper (offline fallback) ──────────────────────────


def _estimate_window(symbol: str, today) -> dict:
    """Coarse offline estimate: pick the next plausible quarter-end
    reporting window for ``symbol``.

    We use a fixed annual cadence (4 reports per year, ~90 days
    apart) starting from a symbol-specific anchor date. The anchor
    is meaningless to the user — what matters is that the returned
    date is in the right ballpark and updates as time passes.
    """
    # Anchor dates chosen so the next report is always >14 days away
    # from "today" in the typical case. The seed values are arbitrary
    # but stable.
    anchors = {
        "SCOM": "2026-05-15",  # Safaricom — often first to report
        "KCB": "2026-05-20",
        "EQTY": "2026-05-25",
        "EABL": "2026-08-15",
        "ABSA": "2026-05-22",
        "SCBK": "2026-05-28",
    }
    try:
        anchor = datetime.fromisoformat(anchors.get(symbol, "2026-05-15")).date()
    except (TypeError, ValueError):
        return {"report_date": "", "status": "unknown", "expected_impact": "low"}

    # Walk forward in 90-day steps until we land in the future.
    while anchor <= today:
        anchor = anchor + timedelta(days=90)
    return {
        "report_date": anchor.isoformat(),
        "status": None,  # will be classified by caller
        "expected_impact": "high" if symbol in {"SCOM", "EQTY", "EABL"} else "medium",
    }
