#!/usr/bin/env python3
"""Post-run monitor for the 10:30 auto-trader-execution cron.

Fires ~3 min after the run. Captures:
  1. Real cron output (last run of bd8361975bd7) — the verbatim execution report.
  2. Diff vs baseline: state.json mtime, cash, positions count, transactions.json mtime.
  3. Whether live-prices cache + Mansa path were reached.

Prints a compact human report to stdout (delivered verbatim by cron).
"""
import json, os, datetime, glob

TR = os.path.expanduser("~/.trading")
CRON_DIR = os.path.expanduser("~/.hermes/cron/output/bd8361975bd7")
BASELINE = os.path.join(TR, "auto-trader-baseline.json")

def mtime(rel):
    fp = os.path.join(TR, rel)
    return os.path.getmtime(fp) if os.path.exists(fp) else None

def iso(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "MISSING"

now = datetime.datetime.now()
print("🕙 Auto-Trader Run Monitor — %s" % now.strftime("%Y-%m-%d %H:%M:%S EAT"))
print("─" * 52)

# 1. Baseline
base = json.load(open(BASELINE)) if os.path.exists(BASELINE) else {}

# 2. Current state
state = json.load(open(os.path.join(TR, "portfolio/state.json")))
cur = {
    "state_mtime": mtime("portfolio/state.json"),
    "cash": state.get("cash"),
    "positions_count": len(state.get("positions", [])),
    "txn_mtime": mtime("portfolio/transactions.json"),
    "symbols": sorted([x.get("symbol") for x in state.get("positions", [])]),
}

state_touched = (cur["state_mtime"] or 0) > (base.get("state_mtime") or 0)
txn_touched = (cur["txn_mtime"] or 0) > (base.get("txn_mtime") or 0)

print("BASELINE  cash=%.2f  positions=%d  @ %s" % (
    base.get("cash", 0), base.get("positions_count", 0), iso(base.get("state_mtime", 0))))
print("NOW       cash=%.2f  positions=%d  @ %s" % (
    cur["cash"], cur["positions_count"], iso(cur["state_mtime"])))
print()
print("state.json updated by run?  %s" % ("✅ YES" if state_touched else "❌ NO"))
print("transactions.json appended?  %s" % ("✅ YES" if txn_touched else "❌ NO"))
if cur["cash"] != base.get("cash"):
    print("  cash delta: %.2f KES" % (cur["cash"] - base.get("cash", 0)))
if cur["symbols"] != base.get("symbols"):
    print("  symbols changed:", list(set(cur["symbols"]) ^ set(base.get("symbols", []))))

# 3. Real cron output
files = sorted(glob.glob(os.path.join(CRON_DIR, "*.md")), key=os.path.getmtime, reverse=True)
print()
print("─" * 52)
if files:
    latest = files[0]
    print("Cron output: %s (run %s)" % (os.path.basename(latest), iso(os.path.getmtime(latest))))
    print("─" * 52)
    txt = open(latest).read().strip()
    # Trim to last ~40 lines so we see the actual trade report
    lines = txt.splitlines()
    print("\n".join(lines[-45:]))
else:
    print("⚠️ No cron output found for bd8361975bd7 yet.")
