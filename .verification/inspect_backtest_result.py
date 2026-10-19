"""Inspect the backtest result: what trades does it record? Are SELLs honest?"""
import json
import sys
from collections import Counter

r = json.load(open('/home/hermes/.trading/backtest_live_result.json'))
print('win_rate:', r.get('win_rate_pct'), 'PF:', r.get('profit_factor'), 'total_trades:', r.get('total_trades'))
trades = r.get('trades', [])
print('side counts:', Counter(t.get('side') for t in trades))
sells = [t for t in trades if t.get('side') == 'SELL']
print('num SELLs:', len(sells))
for t in sells:
    print(f"  {t.get('date')} {t.get('symbol')} price={t.get('price')} shares={t.get('shares')} fee={t.get('fee')} slip={t.get('slippage')} reason={str(t.get('reason'))[:50]}")
# check the realized P&L per sell if present
print('--- keys in a trade:', sorted(trades[0].keys()) if trades else 'none')
