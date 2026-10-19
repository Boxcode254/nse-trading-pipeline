"""Shared helper functions for trading operations.

This module contains common helper functions used across the trading system,
such as date parsing, stale detection, and file utilities.
"""
import datetime
import re
from pathlib import Path


def _axys_file_date(filename: str):
    """Extract ISO date from 'axys_closes_YYYY-MM-DD.json' or None."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename or "")
    return m.group(1) if m else None


def _normalize_report_date(raw: str) -> str | None:
    """Convert '31st July 2026' style AXYS date to ISO '2026-07-31'.

    Returns None if it cannot be parsed (caller falls back to today).
    """
    if not raw:
        return None
    cleaned = re.sub(r"(\d)(st|nd|rd|th)", r"\1", raw.strip())
    # Handle the "Sept" -> "Sep" quirk that caused "Sepember" before
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _is_stale(report_iso: str | None, max_days: int = 3) -> bool:
    """True if the PDF's report date is too old (or future) to apply to today.

    AXYS posts the Daily Market Watch the MORNING AFTER the session it covers,
    so a 1-day gap (yesterday's close, received today) is the NORMAL case and
    must NOT be treated as stale. We block only PDFs older than `max_days`
    (default 3, which tolerates a Friday -> Monday weekend gap) or dated in the
    future. Unparseable dates fall back to today (not stale).
    """
    if not report_iso:
        return False
    try:
        rep_d = datetime.date.fromisoformat(report_iso)
    except ValueError:
        return False
    delta = (datetime.date.today() - rep_d).days
    return delta > max_days or delta < 0