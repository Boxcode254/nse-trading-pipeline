"""Check the live ledger: how many SELL actions exist, and what's their P&L?
The audit says the REAL ledger shows 8/22 sells profitable (36%). Does the
backtest harness read this ledger at all?
"""
import os
import sys
import json
from collections import Counter

sys.path.insert(0, "/home/hermes/.trading")

# 1. Find the live ledger / transactions
ledger_paths = [
    "/home/hermes/.trading/portfolio/transactions.json",
    "/home/hermes/.trading/portfolio/transactions.jsonl",
    "/home/hermes/.trading/transactions.json",
]
for lp in ledger_paths:
    if os.path.exists(lp):
        print("FOUND ledger:", lp)

# 2. Inspect live_strategy_data for any ledger loading
sys.path.insert(0, "/home/hermes/.trading/trading/backtest")
import trading.backtest.live_strategy_data as lsd
print("--- live_strategy_data functions:")
for fn in dir(lsd):
    if not fn.startswith("_"):
        print("  ", fn)

# Check if there's a load_trades / load_ledger function
for fn_name in ["load_trades", "load_ledger", "load_transactions", "load_initial_state"]:
    if hasattr(lsd, fn_name):
        fn = getattr(lsd, fn_name)
        print(f"\n--- {fn_name} source:")
        try:
            import inspect
            print(inspect.getsource(fn)[:1500])
        except Exception as e:
            print("  (no source)", e)
