#!/usr/bin/env python3
"""
fundamental_audit_run.py — Post-execution audit hook for the auto-trader.

Reads today's executed bank trades from paper_orders.json (those whose
client_order_id was created today) + the live book, classifies the
fundamental health of each, and logs the audit record.

Does NOT alter execution. Reads only. Alerts (via stdout, delivered by the
cron) only for DETERIORATING bank executions.

AUDIT-FIRST: this is the Phase 1 audit layer. No trade blocking, no sizing.
"""

import json
import os
import sys
from datetime import datetime, timezone, date
from pathlib import Path

TRADING_ROOT = Path(__file__).resolve().parent.parent
ORDERS_PATH = TRADING_ROOT / "paper_orders.json"
AUDIT_LOG_PATH = TRADING_ROOT / "audit" / "fundamental_audit.json"
BANK_TICKERS = {"KCB", "EQTY", "COOP", "SCBK", "ABSA"}

sys.path.insert(0, str(TRADING_ROOT / "scripts"))
from fundamental_guard import classify, load_fundamentals, load_audit_log, save_audit_log, load_book_symbols  # noqa: E402


def today_order_ids() -> set:
    """Orders executed today (no timestamp field, so use client_order_id's date suffix
    if present, else fall back to 'all' — we only care about bank trades."""
    # paper_orders lacks timestamps; identify bank trades regardless and
    # let the caller decide the window. For a daily hook we audit bank trades
    # seen as FILLED. (Timestamps would be ideal but are not stored.)
    return set()


def main():
    orders = json.load(open(ORDERS_PATH))
    fundamentals = load_fundamentals()
    log = load_audit_log()

    # Book-driven: audit every name current/hypothetically traded that the book
    # holds OR that has a fundamentals row. This covers banks AND non-banks
    # (SCOM, EABL, KPLC, TOTL, KNRE) and any future investment automatically.
    book = load_book_symbols() | set(fundamentals.keys()) - {"_meta"}

    # DEDUP: only audit orders we have NOT already audited. paper_orders.json has
    # no timestamps, so every run used to re-audit ALL orders -> unbounded log
    # growth (144 records from 36 orders). Key on the unique client_order_id.
    already_audited = {r.get("client_order_id") for r in log.get("records", [])}

    bank_trades = []
    for oid, o in orders.items():
        sym = str(o.get("symbol", "")).upper()
        cid = o.get("client_order_id")
        # Skip orders already audited (dedup) and non-book names.
        if cid in already_audited:
            continue
        if sym in book and o.get("status") == "FILLED":
            bank_trades.append({
                "client_order_id": cid,
                "symbol": sym,
                "side": o.get("side"),
                "shares": o.get("filled_quantity") or o.get("quantity"),
                "fill_price": o.get("average_fill_price") or o.get("filled_price"),
                "status": o.get("status"),
            })

    if not bank_trades:
        print("fundamental-audit: no NEW book executions to audit. Silent.")
        return

    det_flagged = []
    records = []
    for t in bank_trades:
        cls = classify(fundamentals.get(t["symbol"]))
        rec = dict(t)
        rec["audit_id"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec["health_at_exec"] = cls["health"]
        rec["reasons"] = cls["reasons"]
        rec["stale"] = cls["stale"]
        records.append(rec)
        if cls["health"] == "DETERIORATING":
            det_flagged.append(t["symbol"])

    log["records"].extend(records)
    save_audit_log(log)

    # Output: only message loudly on DETERIORATING; otherwise brief/quiet
    if det_flagged:
        print(f"fundamental-audit: DETERIORATING bank execution flagged: {sorted(set(det_flagged))}")
        print("  Review these before any further add to the name. (Audit layer, not a block.)")
    else:
        # Quiet line for the log; cron will show it only if configured to surface
        healths = {t["symbol"]: c["health"] for t, c in
                   zip(bank_trades, [classify(fundamentals.get(x["symbol"])) for x in bank_trades])}
        print("fundamental-audit: bank executions classified:", healths)


if __name__ == "__main__":
    main()
