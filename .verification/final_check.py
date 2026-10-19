"""Final consolidated evidence check for the audit report."""
import json
from collections import Counter

# 1. The artifact the audit points at
r = json.load(open('/home/hermes/.trading/backtest_live_result.json'))
print("backtest_live_result.json: win_rate=%s PF=%s trades=%s" % (
    r.get('win_rate_pct'), r.get('profit_factor'), r.get('total_trades')))
trades = r.get('trades', [])
print("  side counts:", Counter(t.get('side') for t in trades))
sells = [t for t in trades if t.get('side') == 'SELL']
print("  SELL count:", len(sells))

# 2. Live ledger honest metrics
txns = json.load(open('/home/hermes/.trading/portfolio/transactions.json'))
if isinstance(txns, dict):
    txns = txns.get("transactions", txns.get("txns", []))
sells_live = [t for t in txns if isinstance(t, dict) and t.get("action") == "SELL"]
wins = [t for t in sells_live if (t.get("realised_pnl") or 0) > 0]
print("live ledger: %d SELLs, %d wins -> win_rate=%.1f%%" % (
    len(sells_live), len(wins), len(wins)/len(sells_live)*100 if sells_live else 0))

# 3. Skip markers in the test tree (any skips that force green?)
import subprocess, os
out = subprocess.run(
    [os.path.expanduser('~/.trading/.venv/bin/python'), '-m', 'pytest', 'trading/tests/', 'tests/', '-q', '-rs'],
    capture_output=True, text=True, cwd='/home/hermes/.trading', timeout=300)
print("\n--- pytest -rs tail:")
print(out.stdout[-800:])
print(out.stderr[-300:])
print("rc:", out.returncode)
