#!/usr/bin/env python3
"""Daily eqw-v2 benchmark tracking step for the 15:30 market-close refresh.

Runs AFTER the MTM refresh so the strategy side and the benchmark side are both
valued off the same settled close. Same run-as-user model as ``refresh-mtm.py``:
invoked as the ``trading`` user with the ABSOLUTE venv python path (the sudoers
NOPASSWD rule matches that exact binary, so bare ``python3`` would break it).

The portfolio dir is trading-owned (640), which is why this must not run as
``hermes``: the record file ``portfolio/benchmark_eqw_v2.json`` is written here.

TOLERANT BY DESIGN — the market-close refresh must never fail because of the
benchmark tracker, so this wrapper always exits 0 and reports what happened.

Reads:  portfolio/axys_closes_<date>.json | mtm_state.json | data/nse_<SYM>.csv
        (via trading.price_source — the AXYS > feed > CSV authority chain)
Writes: portfolio/benchmark_eqw_v2.json   (its own record, nothing else)
"""
from __future__ import annotations

import logging
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        from trading.services.benchmark_eqw_v2 import (
            daily_track,
            format_record_summary,
            load_record,
        )
    except Exception as exc:  # import/perms problem must not break the refresh
        print("eqw-v2 tracker unavailable (non-fatal): %r" % (exc,))
        return 0

    try:
        payload = daily_track()
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        print("eqw-v2 tracker failed (non-fatal): %r" % (exc,))
        return 0

    if payload.get("error"):
        print("eqw-v2 tracker: %s (non-fatal)" % payload["error"])
        return 0

    init = payload.get("init") or {}
    if init.get("initialized"):
        print("eqw-v2: initialised versioned benchmark (init_date=%s)"
              % init["record"].get("init_date"))
        for key, exclusions in (init.get("excluded") or {}).items():
            for e in exclusions:
                print("eqw-v2: %s excluded %s (%s)"
                      % (key, e.get("symbol"), e.get("reason")))
    track = payload.get("track") or {}
    for key, r in (track.get("results") or {}).items():
        print("eqw-v2 %-8s %s value=%s (%s) unpriced=%s"
              % (key, track.get("date"), r.get("value"), r.get("price_source"),
                 r.get("unpriced")))
    if not track.get("written"):
        print("eqw-v2: no row written for %s (see unpriced above)" % track.get("date"))
    rec = load_record()
    if rec:
        print("\n".join(format_record_summary(rec)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
