"""One-shot correction: make state.json cash reconcile to the transaction ledger.

Engine invariant: cash == initial_capital + sum(net_cash_delta over all trades).
The book drifted +KES 2,106 off this (BAMB position added without its cash leg
landing in state.json). We do NOT touch positions or transactions — only the
live cash field — so the fix is contained and reversible from the ledger.

Permission hardening mirrors engine._write_json (640 + parent gid) to avoid the
600-lock regression that previously broke the trading user.
"""
import json
import os
import datetime
from pathlib import Path

P = Path("/home/hermes/.trading/portfolio")
state_p = P / "state.json"
txns = json.loads((P / "transactions.json").read_text())
state = json.loads(state_p.read_text())

INIT = float(state["initial_capital"])
before = float(state["cash"])
correct_cash = round(INIT + sum(t["net_cash_delta"] for t in txns), 2)

state["cash"] = correct_cash
state["updated_at"] = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

tmp = state_p.with_suffix(state_p.suffix + ".tmp")
tmp.write_text(json.dumps(state, indent=2, sort_keys=True, default=str))
os.replace(tmp, state_p)
try:
    os.chmod(state_p, 0o640)
    parent_gid = state_p.parent.stat().st_gid
    if state_p.stat().st_gid != parent_gid:
        os.chown(state_p, -1, parent_gid)
except OSError:
    pass  # best-effort

print(f"cash: {before:,.2f} -> {correct_cash:,.2f}")
print(f"phantom removed: KES {before - correct_cash:,.2f}")
print(f"reconciled to ledger: {abs((INIT + sum(t['net_cash_delta'] for t in txns)) - correct_cash) < 0.01}")
