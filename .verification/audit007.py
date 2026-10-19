"""AUDIT-007 verification: sector caps + full test suite.
1. config.sector_cap('banking') == {warn:40, hard:45}
2. 44% banking -> WARN only; 46% -> HARD + trim (need to trace auto_trader code)
3. Full suite must pass.
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.trading"))
sys.path.insert(0, os.path.join(os.path.expanduser("~/.trading"), "trading"))

from trading import config

caps = config.sector_cap("banking")
print(f"[1] sector_cap('banking') = {caps}")
assert caps == {"warn": 40.0, "hard": 45.0}, f"banking caps wrong: {caps}"

# momentum gate could uplift HARD; check whether it's disabled/neutral today
# (it reads nse_*.csv data; if enabled + trending, hard could become 55)
# For the assert, re-read the raw config (no momentum) vs live call
base_caps = config.EXECUTION_CONFIG["sector_caps"]["banking"]
print(f"[1b] raw config banking caps = {base_caps}")
gate = config.EXECUTION_CONFIG.get("momentum_gate", {})
print(f"[1b] momentum_gate = {gate}")

# 2. Trace auto_trader code path: WARN vs HARD
src = open(os.path.join(os.path.expanduser("~/.trading"), "trading", "auto_trader.py")).read()
# sector warnings are around line 378-398
import re
warn_block = src[src.find("# ── Sector exposure warning"):src.find("# ── Sector exposure warning")+1500]
print("[2] sector exposure warning block:")
print(warn_block)

# HARD trim block around line 896-930
hard_block = src[src.find("Uses tiered per-sector caps"):src.find("Uses tiered per-sector caps")+1500]
print("[2] HARD trim block:")
print(hard_block)

print("AUDIT-007: PARTIAL (suite run separately)")
