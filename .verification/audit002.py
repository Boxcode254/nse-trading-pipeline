"""AUDIT-002 verification: _port_state_for_safety per-share avg_cost + stop-loss sane."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.expanduser("~/.trading"))
sys.path.insert(0, os.path.join(os.path.expanduser("~/.trading"), "trading"))

from trading.auto_trader import _port_state_for_safety
from trading.execution.safety import SafetyEngine

positions = [
    {"symbol": "SCOM", "shares": 402, "avg_cost": 35.5379, "current_value": 14411.7},
    {"symbol": "KNRE", "shares": 2188, "avg_cost": 3.5197, "current_value": 8270.64},
]
port = _port_state_for_safety(positions)
scom = port["positions"]["SCOM"]
print(f"[1] SCOM avg_cost = {scom['avg_cost']} (expect 35.5379)")
print(f"[1] SCOM value = {scom['value']} shares = {scom['shares']}")

tmp = tempfile.mkdtemp(prefix="audit002_")
safety = SafetyEngine({
    "state_dir": tmp,
    "emergency_stop_path": os.path.join(tmp, "EMERGENCY_STOP"),
    "macro_state_path": os.path.join(tmp, "macro_breaker.json"),
})
res = safety.should_stop_loss("SCOM", port)
print(f"[2] SCOM should_stop_loss = {res}")
if res and -100.0 <= res["loss_pct"] <= 100.0:
    print("[2] loss_pct sane (within +-100%)")
else:
    print("[2] FAILED: loss_pct NOT sane")

res2 = safety.should_stop_loss("KNRE", port)
print(f"[2b] KNRE should_stop_loss = {res2}")

# adversarial: what if avg_cost were divided by shares as before?
old = port["positions"]["SCOM"]
bad = dict(old); bad["avg_cost"] = old["avg_cost"] / old["shares"]
res_bad = safety.should_stop_loss("SCOM", {"positions": {"SCOM": bad}})
print(f"[3] (adversarial) if old bug present, loss_pct would be: {res_bad['loss_pct'] if res_bad else None}")

assert scom["avg_cost"] == 35.5379, "avg_cost corrupted"
assert res is not None and -100.0 <= res["loss_pct"] <= 100.0, "loss_pct not sane"
print("AUDIT-002: OK")
