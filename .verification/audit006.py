"""AUDIT-006 verification: refresh-mtm refreshes state.json prices in SANDBOX.
current_value == AXYS x shares for covered symbols; shares/cash/avg_cost untouched.
"""
import os
import sys
import tempfile
import json
import datetime
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.trading"))
sys.path.insert(0, os.path.join(os.path.expanduser("~/.trading"), "trading"))

from trading.price_source import apply_authoritative_prices

d = Path(tempfile.mkdtemp(prefix="audit006_"))
portfolio = d / "portfolio"
data = d / "data"
portfolio.mkdir(); data.mkdir()

state = {
    "cash": 16320.34,
    "initial_capital": 100000.0,
    "positions": [
        {"symbol": "AXYS", "shares": 100, "avg_cost": 30.0, "total_cost": 3000.0, "current_value": 2999.0},
        {"symbol": "ABSA", "shares": 306, "avg_cost": 33.1863, "total_cost": 10155.02, "current_value": 10449.9},
    ],
}
before = json.loads(json.dumps(state))

# Fresh axys_closes file for TODAY with AXYS close 42.5
today = datetime.date.today().isoformat()
(portfolio / f"axys_closes_{today}.json").write_text(json.dumps({"axys": {"AXYS": 42.5}}))

res = apply_authoritative_prices(state, str(portfolio), previous=before)
print(f"[1] sources: {res.sources}")
print(f"[1] AXYS current_value = {state['positions'][0]['current_value']} (expect 100*42.5 = 4250.0)")
print(f"[1] ABSA current_value = {state['positions'][1]['current_value']} (no axys, expect preserved/unchanged fallback)")

# Field-scope check: shares/cash/avg_cost/total_cost untouched
for key in ("cash", "initial_capital"):
    assert state[key] == before[key], f"{key} mutated"
for old, new in zip(before["positions"], state["positions"]):
    for key in ("symbol", "shares", "avg_cost", "total_cost"):
        assert new[key] == old[key], f"{new['symbol']}.{key} mutated: {old[key]} -> {new[key]}"

assert state["positions"][0]["current_value"] == 4250.0, "AXYS price not applied"
print("[2] shares/cash/avg_cost/total_cost untouched: OK")
print("AUDIT-006: OK")
