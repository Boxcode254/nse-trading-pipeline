"""AUDIT-007 nuance: where is the WARN (40%) level actually used?
The audit contract: 44% -> WARN only; 46% -> HARD + trim.
The buy-room guard at auto_trader.py ~1145 uses cap['warn']; the warning banner uses cap['hard'].
"""
import os, sys
sys.path.insert(0, "/home/hermes/.trading")
src = open("/home/hermes/.trading/trading/auto_trader.py").read()

import re
# All uses of cap['warn'] and cap['hard'] in auto_trader
print("--- uses of cap['warn']:")
for m in re.finditer(r"cap\[\"warn\"\]", src):
    line = src.count("\n", 0, m.start()) + 1
    print(f"  line {line}: {src[m.start()-120:m.start()+80].splitlines()[-1][:130]}")

print("--- uses of cap['hard']:")
for m in re.finditer(r"cap\[\"hard\"\]", src):
    line = src.count("\n", 0, m.start()) + 1
    print(f"  line {line}: {src[m.start()-120:m.start()+80].splitlines()[-1][:130]}")

print("\n--- sector warning block lines 378-400 (verbatim):")
print(src[src.find("# ── Sector exposure warning"):src.find("# ── Sector exposure warning")+1500])
