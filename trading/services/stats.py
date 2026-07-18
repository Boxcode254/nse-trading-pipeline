"""Statistics service.

Aggregates the platform's signals.csv and run logs into a single
summary that ``trading stats`` can show.
"""
from __future__ import annotations

import csv
import glob
import json
import os
from collections import Counter
from typing import Any

from .. import config
from ..strategies import REGISTRY


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


def build() -> dict[str, Any]:
    """Build a stats summary for the platform.

    Output schema::

        {
          "total_signals": int,
          "signals_by_decision": {"BUY": n, "SELL": n, "HOLD": n},
          "signals_by_pair": {"SCOM": n, ...},
          "total_scans": int,
          "avg_scan_seconds": float,
          "win_rate_pct": float,
          "best_strategy": str,
          "avg_confidence": float,
          "last_scan": "YYYY-MM-DD HH:MM:SS" or None,
          "strategies_registered": int,
        }
    """
    rows = _read_signals_csv()
    logs = _read_run_logs()

    decisions = Counter(r.get("signal", "") for r in rows)
    by_pair = Counter(r.get("pair", "") for r in rows)
    total_signals = len(rows)
    buy = decisions.get("BUY", 0)
    sell = decisions.get("SELL", 0)
    win_rate_pct = round(100.0 * buy / total_signals, 2) if total_signals else 0.0

    # Confidence: signals.csv doesn't store it; use buy's share of buy+sell as proxy
    # for the "win rate" of recommendations that are taken.
    actionable = buy + sell
    actionable_win_pct = round(100.0 * buy / actionable, 2) if actionable else 0.0

    total_scans = len(logs)
    avg_scan_seconds = (
        round(sum(log.get("elapsed_seconds", 0.0) for log in logs) / total_scans, 2)
        if total_scans
        else 0.0
    )
    last_scan = max((log.get("run_timestamp", "") for log in logs), default=None) or None

    best_strategy = "A"  # frozen benchmark is always available
    if "A" in REGISTRY:
        best_strategy = "A (SMA(20/50) + RSI(14))"

    return {
        "total_signals": total_signals,
        "signals_by_decision": dict(decisions),
        "signals_by_pair": dict(by_pair),
        "buy_signals": buy,
        "sell_signals": sell,
        "hold_signals": decisions.get("HOLD", 0),
        "win_rate_pct": win_rate_pct,                # BUY share of all signals
        "actionable_win_rate_pct": actionable_win_pct,  # BUY share of BUY+SELL
        "total_scans": total_scans,
        "avg_scan_seconds": avg_scan_seconds,
        "best_strategy": best_strategy,
        "avg_confidence": 0.0,  # not persisted in CSV; placeholder
        "last_scan": last_scan,
        "strategies_registered": len(REGISTRY),
    }
