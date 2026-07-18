"""``trading scan`` — run a complete market scan.

Wraps the existing ``cmd_run`` logic from the legacy ``__main__.py``
so all current scan behaviour is preserved.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from ... import config, report as report_mod
from ...fetchers import fetch_data
from ...signals import engine as signal_engine
from ...signals import validator as signal_validator
from ...storage import log as storage_log


def run(quiet: bool = False, as_json: bool = False, verbose: bool = False) -> int:
    """Run the full scan over every configured pair."""
    config.ensure_dirs()
    run_start = time.time()
    rejected: list[dict[str, Any]] = []
    run_pairs: list[str] = []
    run_ok: list[str] = []
    run_failed: list[str] = []
    run_sources: dict[str, str] = {}
    pair_signals: dict[str, dict[str, Any]] = {}

    for pair in config.PAIRS:
        run_pairs.append(pair)
        try:
            df = fetch_data(pair)
        except Exception:
            run_failed.append(pair)
            continue
        run_ok.append(pair)
        run_sources[pair] = df.attrs.get("source", "?")
        try:
            signals = signal_engine.generate_signals(df, pair=pair)
        except Exception:
            continue
        if not signals:
            continue
        accepted, pair_rejected = signal_validator.filter_signals(signals, df)
        rejected.extend(pair_rejected)
        if not accepted:
            continue
        current = accepted[-1]
        storage_log.log_signal(current)
        pair_signals[pair] = current

    run_end = time.time()
    summary = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(run_end - run_start, 2),
        "pairs_scanned": len(run_pairs),
        "pairs_with_data": len(run_ok),
        "pairs_failed": len(run_failed),
        "signals_accepted": len(pair_signals),
        "signals_rejected": len(rejected),
        "data_sources": run_sources,
    }

    if as_json:
        if pair_signals:
            summary["latest_signals"] = _summarise(list(pair_signals.values()))
        if rejected:
            summary["rejected_signals"] = _summarise(rejected)
        print(json.dumps(summary, indent=2, default=str))
        return 0

    if pair_signals:
        print(report_mod.format_daily_report(pair_signals, rejected,
                                              run_start=run_start, run_end=run_end))
    else:
        print("⚠️  No signals produced. Check your data source and try again.")
        return 1
    return 0


def _summarise(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in signals:
        out.append({
            "symbol": s.get("pair", "").replace("/", ""),
            "decision": s.get("signal", ""),
            "price": s.get("price"),
            "rsi": s.get("rsi"),
            "confidence": s.get("confidence", 0),
        })
    return out
