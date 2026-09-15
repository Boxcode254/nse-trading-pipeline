"""Current book-integrity status record (TP-008).

``portfolio/book_integrity.log`` is an append-only *incident* log, not a status
feed: its newest line can be weeks old and describe a divergence that was fixed
long ago.  Reporting the tail of that file as "current integrity" presented a
stale failure as a live one.

This module owns the always-current record instead:

    portfolio/integrity_status.json
    {
      "checked_at":      "2026-09-15T12:00:00+03:00",  # last scheduled run
      "status":          "ok" | "divergences" | "no_source" | "error",
      "source_file":     "axys_closes_2026-09-14.json" | null,
      "source_date":     "2026-09-14" | null,
      "positions_checked": int,
      "tolerance_pct":   float,
      "detail":          str | null,
      "divergences":     [{"symbol", "book_price", "official", "delta_pct"}],
      "last_failure":    {checked_at, status, source_file, source_date,
                          summary, divergences} | null
    }

Contract:

* every scheduled run of ``scripts/book_integrity_check.py`` writes the record
  (the log is still appended for incidents, unchanged);
* ``last_failure`` is set by a failing run and is NEVER cleared by a later
  ``ok`` run — the report shows it separately, with its own age;
* ``format_report_lines()`` renders current status + computed ages, and never
  invents a date or a staleness claim;
* reads are tolerant: a missing/corrupt record yields ``None``, never a crash.

Nothing here writes anywhere except the status file it is handed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

STATUS_FILENAME = "integrity_status.json"

STATUS_OK = "ok"
STATUS_DIVERGENCES = "divergences"
STATUS_NO_SOURCE = "no_source"
STATUS_ERROR = "error"

_STATUS_LABELS = {
    STATUS_OK: "ok",
    STATUS_DIVERGENCES: "DIVERGENCES",
    STATUS_NO_SOURCE: "NO OFFICIAL SOURCE",
    STATUS_ERROR: "ERROR",
}


def status_path(portfolio_dir: str | os.PathLike) -> Path:
    """Absolute path of the status record inside *portfolio_dir*."""
    return Path(portfolio_dir) / STATUS_FILENAME


# ── time helpers ─────────────────────────────────────────────


def now_iso() -> str:
    """Current time as an ISO-8601 string with the local UTC offset."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 stamp; ``None`` when unparseable/absent."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt


def _as_aware(dt: datetime) -> datetime:
    """Naive stamps are read as local time (that is how they were written)."""
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt


def age_days(checked_at: Any, now: Optional[datetime] = None) -> Optional[float]:
    """Age of *checked_at* in days (float, 2dp); ``None`` if unparseable."""
    dt = parse_ts(checked_at)
    if dt is None:
        return None
    ref = _as_aware(now) if now is not None else datetime.now().astimezone()
    return round((ref - _as_aware(dt)).total_seconds() / 86400.0, 2)


def human_age(checked_at: Any, now: Optional[datetime] = None) -> Optional[str]:
    """'3.2h ago' / '2.4d ago' / 'just now'; ``None`` when unparseable.

    Computes from the RAW delta — rounding inside ``age_days()`` (2dp of a
    day) quantises an hour to 0.04d, which sat below the 1-hour "just now"
    cutoff and misrendered a 60-minute-old check as "just now".
    """
    dt = parse_ts(checked_at)
    if dt is None:
        return None
    ref = _as_aware(now) if now is not None else datetime.now().astimezone()
    hours = (ref - _as_aware(dt)).total_seconds() / 3600.0
    if hours < 0:
        return "in the future"
    if hours < 1.0:
        return "just now"
    if hours < 24.0:
        return "%.1fh ago" % hours
    return "%.1fd ago" % (hours / 24.0)


def short_stamp(value: Any) -> str:
    """'YYYY-MM-DDTHH:MM' (or the raw string) for display."""
    dt = parse_ts(value)
    if dt is None:
        return str(value) if value else "unknown"
    return dt.strftime("%Y-%m-%dT%H:%M")


# ── record build / read / write ──────────────────────────────


def build(
    *,
    status: str,
    checked_at: Optional[str] = None,
    source_file: Optional[str] = None,
    source_date: Optional[str] = None,
    positions_checked: int = 0,
    tolerance_pct: float = 1.0,
    divergences: Optional[list[dict[str, Any]]] = None,
    detail: Optional[str] = None,
    previous: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a status record; carries a prior ``last_failure`` forward.

    ``last_failure`` is overwritten only by a failing run, so an ``ok`` run
    never erases the incident history.
    """
    checked_at = checked_at or now_iso()
    divs = [dict(d) for d in (divergences or [])]

    prior_failure = None
    if isinstance(previous, dict):
        prior = previous.get("last_failure")
        if isinstance(prior, dict) and prior:
            prior_failure = prior

    last_failure = prior_failure
    if status != STATUS_OK:
        last_failure = {
            "checked_at": checked_at,
            "status": status,
            "source_file": source_file,
            "source_date": source_date,
            "summary": _summarise(status, divs, detail),
            "divergences": divs,
        }

    return {
        "checked_at": checked_at,
        "status": status,
        "source_file": source_file,
        "source_date": source_date,
        "positions_checked": int(positions_checked),
        "tolerance_pct": float(tolerance_pct),
        "detail": detail,
        "divergences": divs,
        "last_failure": last_failure,
    }


def _summarise(status: str, divergences: list[dict[str, Any]], detail: Optional[str]) -> str:
    if status == STATUS_DIVERGENCES:
        syms = ", ".join(str(d.get("symbol")) for d in divergences[:4])
        more = "" if len(divergences) <= 4 else ", +%d more" % (len(divergences) - 4)
        return "%d divergence(s) (%s%s)" % (len(divergences), syms, more)
    if status == STATUS_NO_SOURCE:
        return detail or "no official AXYS close available"
    if status == STATUS_ERROR:
        return detail or "integrity check failed to run"
    return detail or "ok"


def load(portfolio_dir: str | os.PathLike) -> Optional[dict[str, Any]]:
    """Read the status record; ``None`` when absent or unreadable."""
    path = status_path(portfolio_dir)
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or "status" not in data:
        return None
    return data


def write(portfolio_dir: str | os.PathLike, record: dict[str, Any]) -> Path:
    """Atomically persist *record*; creates the directory if needed."""
    path = status_path(portfolio_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return path


def record(
    portfolio_dir: str | os.PathLike,
    *,
    status: str,
    checked_at: Optional[str] = None,
    source_file: Optional[str] = None,
    source_date: Optional[str] = None,
    positions_checked: int = 0,
    tolerance_pct: float = 1.0,
    divergences: Optional[list[dict[str, Any]]] = None,
    detail: Optional[str] = None,
) -> dict[str, Any]:
    """Read the previous record, build the new one and write it."""
    previous = load(portfolio_dir)
    built = build(
        status=status,
        checked_at=checked_at,
        source_file=source_file,
        source_date=source_date,
        positions_checked=positions_checked,
        tolerance_pct=tolerance_pct,
        divergences=divergences,
        detail=detail,
        previous=previous,
    )
    write(portfolio_dir, built)
    return built


# ── rendering ────────────────────────────────────────────────


def format_report_lines(
    record: Optional[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    stale_after_days: float = 2.0,
) -> list[str]:
    """Report lines: CURRENT status first, last recorded failure second.

    Ages are always computed from the stamps in the record; no date, age or
    staleness string is ever hardcoded.  Returns bare lines (the caller adds
    its own bullets).
    """
    if not record:
        return [
            "Current integrity: no status record on file yet "
            "(portfolio/%s — written by scripts/book_integrity_check.py)" % STATUS_FILENAME
        ]

    status = str(record.get("status") or "unknown")
    label = _STATUS_LABELS.get(status, status.upper())
    checked_at = record.get("checked_at")
    age = human_age(checked_at, now=now)
    stamp = short_stamp(checked_at)
    age_days = age_days_of(record, now=now)

    bits = ["Current integrity: %s" % label]
    bits.append("checked %s" % stamp)
    if age:
        bits.append(age)
    if age_days is not None and age_days > stale_after_days:
        bits.append("STALE (no run in >%.1fd)" % stale_after_days)
    if record.get("source_file"):
        src = record["source_file"]
        if record.get("source_date"):
            src = "%s (%s)" % (src, record["source_date"])
        bits.append("source %s" % src)
    elif status == STATUS_NO_SOURCE:
        bits.append("source: none found")
    bits.append("positions checked %s" % record.get("positions_checked", 0))
    tol = record.get("tolerance_pct")
    if isinstance(tol, (int, float)):
        bits.append("tol %.1f%%" % tol)
    lines = [" · ".join(bits)]

    divs = record.get("divergences") or []
    if status == STATUS_DIVERGENCES and divs:
        for d in divs:
            lines.append(
                "  %s book=%.2f official=%s delta=%+.2f%%"
                % (
                    d.get("symbol"),
                    float(d.get("book_price") or 0.0),
                    d.get("official"),
                    float(d.get("delta_pct") or 0.0),
                )
            )
    elif status == STATUS_ERROR and record.get("detail"):
        lines.append("  %s" % record["detail"])

    lf = record.get("last_failure")
    if isinstance(lf, dict) and lf:
        lf_age = human_age(lf.get("checked_at"), now=now)
        lf_line = "Last recorded failure: %s" % short_stamp(lf.get("checked_at"))
        if lf_age:
            lf_line += " (%s)" % lf_age
        summary = lf.get("summary")
        if summary:
            lf_line += " · %s" % summary
        if lf.get("source_file"):
            lf_line += " · vs %s" % lf["source_file"]
        lines.append(lf_line)
    else:
        lines.append("Last recorded failure: none recorded")

    return lines


def age_days_of(record: dict[str, Any], *, now: Optional[datetime] = None) -> Optional[float]:
    """Age in days of a record's ``checked_at`` (convenience for callers)."""
    return age_days(record.get("checked_at"), now=now)
