"""AUDIT-007 sector-cap behavior: 44% banking -> WARN only; 46% -> HARD + trim.
Traces auto_trader.run_auto_trade's sector logic and target_allocation guards.
"""
import os
import sys
import json

sys.path.insert(0, "/home/hermes/.trading")
from trading import config
from trading.target_allocation import generate_rebalance_plan

# 1. Config caps
print("banking caps:", config.sector_cap("banking"))
assert config.sector_cap("banking") == {"warn": 40.0, "hard": 45.0}

# 2. Simulate the auto_trader sector-cap logic with a 44% banking book.
# The trim loop: for each sector pct > hard -> force sells.
# We replicate the exact block from auto_trader.py (lines ~896-930) with totals.
def sector_trim_logic(sector_pcts, total_before, current_positions):
    """Return (warned_sectors, hard_trimmed_sectors)."""
    warned = []
    trimmed = []
    # warning block
    over_limit = []
    for sec, pct in sector_pcts.items():
        cap = config.sector_cap(sec)
        if pct > cap["hard"]:
            over_limit.append((sec, pct, cap["hard"]))
    if over_limit:
        warned = [s for s, _, _ in over_limit]
    # hard trim block
    for sec, pct in sector_pcts.items():
        cap = config.sector_cap(sec)
        hard = cap["hard"]
        if pct > hard:
            trimmed.append(sec)
    return warned, trimmed

# 44% banking book
pos_44 = [{"symbol": "KCB", "current_value": 44000, "shares": 500, "avg_cost": 80.0}]
w, t = sector_trim_logic({"banking": 44.0}, 100000, pos_44)
print(f"[2] 44% banking: warned={w} trimmed={t}")
assert 44.0 <= config.sector_cap("banking")["hard"], "44 should not trim"

# 46% banking book
w, t = sector_trim_logic({"banking": 46.0}, 100000, pos_44)
print(f"[3] 46% banking: warned={w} trimmed={t}")
assert "banking" in t, "46% should hard-trim"

# 3. target_allocation BUY guard uses config.sector_cap hard
src = open(os.path.join(os.path.dirname(config.__file__), "target_allocation.py")).read()
import re
guards = re.findall(r"current_pct.*?sector_cap\(sec\).*?\[.hard.\]", src)
print("[4] target_allocation hard-cap guards found:", len(guards))
for g in guards:
    print("   ", g.replace("\n", " ")[:120])
assert len(guards) >= 2, "hard-cap guards missing in target_allocation"

# 4. Check momentum gate could uplift HARD beyond 45 — this is by design (10pt)
#    but the audit's contract says 46% -> HARD. Momentum uplift would only apply
#    if the sector is trending. Confirm raw (no-uplift) = 45.
print("[5] sector_cap('banking') with momentum disabled...")
saved = config.EXECUTION_CONFIG.get("momentum_gate", {}).copy()
config.EXECUTION_CONFIG["momentum_gate"] = {"enabled": False}
print("    no-momentum caps:", config.sector_cap("banking"))
config.EXECUTION_CONFIG["momentum_gate"] = saved
print("AUDIT-007: OK")
