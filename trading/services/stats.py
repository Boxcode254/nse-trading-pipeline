"""Statistics service.

Aggregates the platform's signals.csv and run logs into a single
summary that ``trading stats`` can show.

Honest-metrics contract (TP-006, 2026-09-15)
--------------------------------------------
Two different things were being conflated under the word "win rate":

* the *signal mix* — what share of emitted signals are BUY.  That is a
  description of the signal engine, not of outcomes.  It is reported as
  ``buy_signal_share_pct`` / ``buy_share_of_actionable_pct``.
* the *outcome performance* — wins / (wins + losses) over CLOSED, evaluated
  outcomes, with the numerator, denominator and coverage exposed.

An outcome win rate is only published when there are at least
:data:`MIN_EVALUATED_OUTCOMES` closed outcomes; below that the value is
``None`` and ``sample_status`` is ``insufficient_sample``.  No metric here is
hardcoded: confidence is not persisted in signals.csv, so no average
confidence is reported at all, and no "best strategy" is claimed without
per-strategy evaluated outcomes to back it.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sqlite3
from collections import Counter
from typing import Any, Optional

from .. import config
from ..strategies import REGISTRY


#: Minimum closed (evaluated) outcomes before any outcome rate is published.
MIN_EVALUATED_OUTCOMES = 30

#: Outcome/recommendation store read (read-only) for the outcome block.
LEARNING_DB_PATH = os.path.expanduser("~/.trading/learning/learning.db")

#: Actionable recommendations — the only ones an outcome can close against.
_ACTIONABLE = ("BUY", "SELL")


def _read_signals_csv() -> list[dict[str, str]]:
    """Read the signals.csv log; empty list if missing or unreadable."""
    if not os.path.exists(config.SIGNALS_CSV):
        return []
    try:
        with open(config.SIGNALS_CSV, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _read_run_logs() -> list[dict[str, Any]]:
    """Read every run log under ``config.LOGS_DIR``."""
    if not os.path.isdir(config.LOGS_DIR):
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(config.LOGS_DIR, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                out.extend(data)
            elif isinstance(data, dict):
                out.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _read_learning_counts(db_path: Optional[str] = None) -> Optional[dict[str, int]]:
    """Read recommendation/outcome counts from the learning store.

    Read-only (``mode=ro`` URI): this must never create or modify state — a
    stats query is a read.  Returns ``None`` when the store is unavailable, so
    callers can say "unavailable" instead of inventing zeroes.
    """
    path = db_path or LEARNING_DB_PATH
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error:
        return None
    try:
        total = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
        actionable = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE recommendation IN (?, ?)",
            _ACTIONABLE,
        ).fetchone()[0]
        row = conn.execute(
            "SELECT COUNT(*) AS evaluated, COALESCE(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), 0) AS wins"
            " FROM outcomes"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    evaluated, wins = int(row[0] or 0), int(row[1] or 0)
    return {
        "recommendations_total": int(total or 0),
        "eligible_sample": int(actionable or 0),
        "evaluated_outcomes": evaluated,
        "wins": wins,
        "losses": max(evaluated - wins, 0),
    }


def _outcome_performance(db_path: Optional[str] = None) -> dict[str, Any]:
    """Outcome block: numerator, denominator, coverage, sample caveat."""
    used = db_path or LEARNING_DB_PATH
    try:
        source = os.path.relpath(used, os.path.expanduser("~/.trading"))
        if source.startswith(".."):
            source = used
    except ValueError:
        source = used
    counts = _read_learning_counts(db_path)
    if counts is None:
        return {
            "available": False,
            "source": source,
            "sample_status": "unavailable",
            "win_rate_pct": None,
            "caveat": "outcome store unavailable: %s not readable" % source,
        }

    evaluated = counts["evaluated_outcomes"]
    wins = counts["wins"]
    losses = counts["losses"]
    total = counts["recommendations_total"]
    eligible = counts["eligible_sample"]
    sufficient = evaluated >= MIN_EVALUATED_OUTCOMES

    coverage_pct = round(100.0 * evaluated / total, 2) if total else 0.0
    eligible_coverage_pct = round(100.0 * evaluated / eligible, 2) if eligible else 0.0
    win_rate_pct = round(100.0 * wins / evaluated, 2) if (sufficient and evaluated) else None
    caveat = (
        "win_rate_pct = wins/(wins+losses) over %d closed outcome(s)" % evaluated
        if sufficient
        else "insufficient_sample (evaluated=%d of %d recommendations, %.2f%% coverage)"
        % (evaluated, total, coverage_pct)
    )

    return {
        "available": True,
        "source": source,
        "recommendations_total": total,
        "eligible_sample": eligible,
        "unresolved_sample": max(eligible - evaluated, 0),
        "evaluated_outcomes": evaluated,
        "wins": wins,
        "losses": losses,
        "numerator": wins,
        "denominator": evaluated,
        "coverage_pct": coverage_pct,
        "eligible_coverage_pct": eligible_coverage_pct,
        "win_rate_pct": win_rate_pct,
        "min_evaluated_outcomes": MIN_EVALUATED_OUTCOMES,
        "sample_status": "sufficient" if sufficient else "insufficient_sample",
        "caveat": caveat,
        "confidence_caveat": (
            "signals.csv does not persist signal confidence; no average confidence is reported"
        ),
    }


def _best_strategy(perf: dict[str, Any]) -> str:
    """Evidence-derived best strategy, or an explicit unavailability label."""
    if not perf.get("available"):
        return "unavailable: outcome store unavailable"
    if perf.get("sample_status") != "sufficient":
        return "unavailable: insufficient evaluated outcomes"
    # The learning store attributes outcomes to symbols, not to strategies —
    # so there is still no defensible "best strategy" to name.
    return "unavailable: no per-strategy outcome attribution in the learning store"


def build(db_path: Optional[str] = None) -> dict[str, Any]:
    """Build a stats summary for the platform.

    Output schema::

        {
          "total_signals": int,
          "signals_by_decision": {"BUY": n, "SELL": n, "HOLD": n},
          "signals_by_pair": {"SCOM": n, ...},
          "buy_signals": int, "sell_signals": int, "hold_signals": int,
          "buy_signal_share_pct": float,          # BUY share of ALL signals
          "buy_share_of_actionable_pct": float,   # BUY share of BUY+SELL
          "outcome_performance": {...},           # outcome-based, see above
          "best_strategy": str,                   # evidence-derived or unavailable
          "total_scans": int,
          "avg_scan_seconds": float,
          "last_scan": "YYYY-MM-DD HH:MM:SS" or None,
          "strategies_registered": int,
        }

    ``buy_signal_share_pct`` and ``buy_share_of_actionable_pct`` are SIGNAL
    MIX, not win rates.  The only win rate in this payload lives under
    ``outcome_performance`` and is derived from evaluated outcomes.
    """
    rows = _read_signals_csv()
    logs = _read_run_logs()

    decisions = Counter(r.get("signal", "") for r in rows)
    by_pair = Counter(r.get("pair", "") for r in rows)
    total_signals = len(rows)
    buy = decisions.get("BUY", 0)
    sell = decisions.get("SELL", 0)
    buy_signal_share_pct = round(100.0 * buy / total_signals, 2) if total_signals else 0.0
    actionable = buy + sell
    buy_share_of_actionable_pct = round(100.0 * buy / actionable, 2) if actionable else 0.0

    total_scans = len(logs)
    avg_scan_seconds = (
        round(sum(log.get("elapsed_seconds", 0.0) for log in logs) / total_scans, 2)
        if total_scans
        else 0.0
    )
    last_scan = max((log.get("run_timestamp", "") for log in logs), default=None) or None

    perf = _outcome_performance(db_path)

    return {
        "total_signals": total_signals,
        "signals_by_decision": dict(decisions),
        "signals_by_pair": dict(by_pair),
        "buy_signals": buy,
        "sell_signals": sell,
        "hold_signals": decisions.get("HOLD", 0),
        # SIGNAL MIX — explicitly not a win rate.
        "buy_signal_share_pct": buy_signal_share_pct,
        "buy_share_of_actionable_pct": buy_share_of_actionable_pct,
        "outcome_performance": perf,
        "total_scans": total_scans,
        "avg_scan_seconds": avg_scan_seconds,
        "best_strategy": _best_strategy(perf),
        "last_scan": last_scan,
        "strategies_registered": len(REGISTRY),
    }
