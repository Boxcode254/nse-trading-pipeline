#!/usr/bin/env python3
"""
fundamental_guard.py — Fundamental intel as an AUDIT layer for the NSE paper book.

Reads a maintained fundamentals store and classifies each bank's health as
HEALTHY / WATCH / DETERIORATING / NO_DATA. It then audits the auto-trader's
bank executions by attaching a health classification + reasons to each trade.

DESIGN PRINCIPLES (best practice: negative screening / fundamental risk filter):
- This is a FILTER (defensive, "is this name on a clear downward trajectory?")
  NOT a SCREENER ("which bank should I own"). It does not pick winners.
- AUDIT-FIRST: it records and flags. It does NOT block trades, alter sizing,
  or touch portfolio/state.json in Phase 1.
- 2-print rule: DETERIORATING requires >=2 consecutive deterioration signals
  (NPL rising 2 prints, OR PAT falling 2 prints, OR a dividend cut). A single
  print is only WATCH. Protects against one-off noise.
- NO_DATA is a safe fallback: never blocks, never alarms.
- Staleness: fundamentals older than STALENESS_MONTHS are downgraded to NO_DATA.

Usage:
  fundamental_guard.py classify --symbol KCB
  fundamental_guard.py classify --all
  fundamental_guard.py audit --trades trades.json   # attach health to trades
  fundamental_guard.py report --since 4w            # evidence summary

CONTEXT-ONLY. See ~/.hermes/plans/2026-08-27_0945-fundamental-intel-audit-layer.md.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────
TRANSDIR = Path(__file__).resolve().parent.parent
FUNDAMENTALS_PATH = TRANSDIR / "news" / "fundamentals.json"
AUDIT_LOG_PATH = TRANSDIR / "audit" / "fundamental_audit.json"

# ── config ────────────────────────────────────────────────────────────
STALENESS_MONTHS = 9          # as_of older than this => NO_DATA
PAT_DROP_THRESHOLD_PCT = -5.0 # PAT yoy below this counts as a deterioration
CAP_NEAR_MIN_MARGIN_PCT = 1.05  # capital within 5% of regulatory min => WATCH

# Sector map — mirrors the pipeline's own SECTOR_MAP (config.py). The audit
# is BOOK-DRIVEN, not hardcoded to banks. It audits every held name that has
# a fundamentals row; names without verified data fall back to NO_DATA safely.
# This is the canonical sector vocabulary the pipeline uses (config.get_sector).
SECTOR_OF = {
    "SCOM": "telecom",
    "KCB": "banking",
    "EQTY": "banking",
    "ABSA": "banking",
    "SCBK": "banking",
    "COOP": "banking",
    "EABL": "consumer",
    "KPLC": "energy",
    "TOTL": "energy",
    "KNRE": "insurance",
    "BRIT": "insurance",
    "WTK": "services",
    "BAMB": "consumer",  # suspended NSE — classify for reporting only
}

# Bank signals are the cleanest / most-validated set; other sectors reuse the
# generic PAT/dividend rule. Per-sector signal nuance can be added later.
BANK_SECTOR = "banking"

# Book tickers are resolved at runtime from mtm_state.json, not hardcoded here.
# This constant is only the DEFAULT/seed audit set (kept for backward compat).
DEFAULT_AUDIT_SET = set(SECTOR_OF.keys())


# ── helpers ───────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_fundamentals() -> dict:
    if not FUNDAMENTALS_PATH.exists():
        return {}
    with open(FUNDAMENTALS_PATH) as f:
        return json.load(f)


def load_audit_log() -> dict:
    if not AUDIT_LOG_PATH.exists():
        return {"records": []}
    with open(AUDIT_LOG_PATH) as f:
        return json.load(f)


def save_audit_log(log: dict):
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def _is_stale(as_of: str) -> bool:
    """True if the as_of date is older than STALENESS_MONTHS."""
    if not as_of:
        return True
    try:
        d = datetime.strptime(str(as_of)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * STALENESS_MONTHS)
    return d < cutoff


MTM_PATH = TRANSDIR / "portfolio" / "mtm_state.json"

def load_book_symbols() -> set:
    """Return the set of tickers currently held in the live book (mtm_state.json).
    The audit is BOOK-DRIVEN: it covers whatever the portfolio holds today, so
    a future investment is audited automatically with no reconfiguration."""
    if not MTM_PATH.exists():
        return set()
    try:
        with open(MTM_PATH) as f:
            data = json.load(f)
        positions = data.get("positions", [])
        return {str(p.get("symbol", "")).upper() for p in positions if p.get("symbol")}
    except (json.JSONDecodeError, OSError):
        return set()


# ── classifier ────────────────────────────────────────────────────────
def classify(fund: dict) -> dict:
    """
    Return {'health': <HEALTHY|WATCH|DETERIORATING|NO_DATA>, 'reasons': [...], 'stale': bool}.

    Deterministic, no ML. Requires >=2 deterioration prints for DETERIORATING.
    Capital near-min is WATCH only. Missing data is NO_DATA (safe).
    """
    if not fund:
        return {"health": "NO_DATA", "reasons": ["no fundamental data"], "stale": False}

    as_of = fund.get("as_of")
    stale = _is_stale(as_of)
    pat = fund.get("pat") or {}
    npl = fund.get("npl_ratio") or {}
    cap = fund.get("capital_ratio") or {}
    div = fund.get("dividend_action") or {}

    # If stale and no meaningful newer data, NO_DATA.
    has_data = bool(pat.get("yoy_pct") is not None or npl.get("trend") or cap.get("latest") is not None)
    if stale and has_data:
        return {"health": "NO_DATA", "reasons": [f"fundamentals stale (as_of {as_of})"], "stale": True}
    if not has_data:
        return {"health": "NO_DATA", "reasons": ["no fundamental data"], "stale": stale}

    reasons = []
    deteriorations = 0

    # PAT deterioration signal (1 of 3)
    yoy = pat.get("yoy_pct")
    if isinstance(yoy, (int, float)) and yoy < PAT_DROP_THRESHOLD_PCT:
        deteriorations += 1
        reasons.append(f"PAT {yoy:+.1f}% YoY (declining)")

    # NPL trend deterioration signal (1 of 3)
    trend = npl.get("trend")
    if isinstance(trend, str) and trend.lower() in ("rising", "deteriorating"):
        deteriorations += 1
        reasons.append(f"NPL trend {trend} (latest {npl.get('latest')})")

    # Dividend cut signal (1 of 3)
    div_recent = div.get("recent")
    if div_recent == "cut":
        deteriorations += 1
        reasons.append("dividend cut")

    # Capital near/below regulatory min => WATCH (never DETERIORATING on its own)
    cap_latest = cap.get("latest")
    cap_min = cap.get("min")
    cap_watch = False
    if isinstance(cap_latest, (int, float)) and isinstance(cap_min, (int, float)) and cap_min:
        if cap_latest < cap_min * CAP_NEAR_MIN_MARGIN_PCT:
            cap_watch = True
            reasons.append(f"capital {cap_latest:.2f}% near min {cap_min:.2f}%")

    if deteriorations >= 2:
        return {"health": "DETERIORATING", "reasons": reasons, "stale": stale}
    if deteriorations == 1 or cap_watch:
        return {"health": "WATCH", "reasons": reasons, "stale": stale}
    if deteriorations == 0 and isinstance(yoy, (int, float)) and yoy >= 0:
        return {"health": "HEALTHY", "reasons": ["PAT stable/growing"], "stale": stale}
    return {"health": "WATCH", "reasons": reasons, "stale": stale}


def classify_symbol(symbol: str, fundamentals: dict) -> dict:
    fund = fundamentals.get(symbol.upper())
    return classify(fund)


# ── audit mode: attach health to executed trades ─────────────────────
def audit_trades(trades: list, fundamentals: dict) -> list:
    """Attach {health, reasons, stale} to each trade record. Returns enriched list."""
    enriched = []
    for t in trades:
        sym = str(t.get("symbol", "")).upper()
        rec = dict(t)
        # Audit ANY name that has a fundamentals row (book-driven, not bank-only).
        # Names without verified data are classified NO_DATA (safe fallback),
        # never skipped silently, never blocked.
        cls = classify(fundamentals.get(sym))
        rec["health_at_exec"] = cls["health"]
        rec["reasons"] = cls["reasons"]
        rec["stale"] = cls["stale"]
        rec["sector"] = SECTOR_OF.get(sym, "other")
        enriched.append(rec)
    return enriched


def audit_and_log(trades: list, fundamentals: dict):
    """Run audit, append to the audit log, return the records."""
    enriched = audit_trades(trades, fundamentals)
    log = load_audit_log()
    for rec in enriched:
        rec["audit_id"] = now_iso()
        log["records"].append(rec)
    save_audit_log(log)
    return enriched


# ── report mode ───────────────────────────────────────────────────────
def build_report(since_days: int = 28) -> str:
    log = load_audit_log()
    records = log.get("records", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    recent = [
        r for r in records
        if _parse_dt(r.get("run_at") or r.get("audit_id")) >= cutoff
    ]
    by_health = {}
    for r in recent:
        h = r.get("health_at_exec", "?")
        by_health[h] = by_health.get(h, 0) + 1
    lines = [f"Fundamental audit report (last {since_days}d): {len(recent)} bank executions"]
    for h, n in sorted(by_health.items(), key=lambda x: -x[1]):
        lines.append(f"  {h}: {n}")
    det = [r for r in recent if r.get("health_at_exec") == "DETERIORATING"]
    if det:
        lines.append("DETERIORATING executions: " + ", ".join(
            f"{r.get('symbol')} ({r.get('side')})" for r in det))
    else:
        lines.append("No DETERIORATING executions in window.")
    fail = [r for r in recent if r.get("health_at_exec") in ("WATCH",)]
    if fail:
        lines.append(f"WATCH executions (single-print): {len(fail)} — monitor, not action.")
    return "\n".join(lines)


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


# ── CLI ───────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Fundamental intel audit layer (context-only).")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify", help="Classify a symbol's fundamental health")
    c.add_argument("--symbol", help="Ticker to classify")
    c.add_argument("--all", action="store_true", help="Classify all bank tickers")

    a = sub.add_parser("audit", help="Attach health to a trades list")
    a.add_argument("--trades", help="Path to trades JSON (list of {symbol, side, ...})")

    r = sub.add_parser("report", help="Evidence summary")
    r.add_argument("--since", default="4", help="Days to look back (default 4 = 4 weeks)")

    args = p.parse_args()
    fundamentals = load_fundamentals()

    if args.cmd == "classify":
        if args.symbol:
            sym = args.symbol.upper()
            print(json.dumps({"symbol": sym, **classify(fundamentals.get(sym))}, indent=2))
        else:
            # Classify all tickers with a fundamentals row + the book's held names.
            book = load_book_symbols()
            all_syms = sorted(set(fundamentals.keys()) - {"_meta"} | book | {"IMPL"})
            for sym in all_syms:
                print(f"{sym}: {classify(fundamentals.get(sym))}")
    elif args.cmd == "audit":
        if not args.trades:
            print("ERROR: --trades required", file=sys.stderr)
            sys.exit(2)
        trades = json.load(open(args.trades))
        enriched = audit_and_log(trades, fundamentals)
        print(json.dumps(enriched, indent=2))
    elif args.cmd == "report":
        print(build_report(int(args.since) * 7))


if __name__ == "__main__":
    main()
