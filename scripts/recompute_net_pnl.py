import json
from pathlib import Path

P = Path("/home/hermes/.trading/portfolio")
state = json.loads((P / "state.json").read_text())
txns = json.loads((P / "transactions.json").read_text())
axys = json.loads((P / "axys_closes_2026-08-04.json").read_text())["axys"]

import importlib.util
spec = importlib.util.spec_from_file_location("cfg", "/home/hermes/.trading/trading/config.py")
cfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfg)

INIT = state["initial_capital"]
cash = state["cash"]
pos = {p["symbol"]: p for p in state["positions"]}

net_deltas = sum(t["net_cash_delta"] for t in txns)
ledger_cash = INIT + net_deltas
total_fees = sum(t.get("fee", 0) for t in txns)
total_slip = sum(cfg.trade_cost(t["total"], t["price"])["slippage"] for t in txns)
total_drag = total_fees + total_slip

holdings = 0.0
rows = []
for sym, p in pos.items():
    px = axys.get(sym, p["avg_cost"])
    mv = round(p["shares"] * px, 2)
    holdings += mv
    rows.append((sym, p["shares"], px, mv, p["total_cost"]))
holdings = round(holdings, 2)
equity = round(cash + holdings, 2)
net = round(equity - INIT, 2)
net_pct = round(net / INIT * 100, 4)
stored_cost = sum(p["total_cost"] for p in pos.values())

print("=== LEDGER INTEGRITY ===")
print(f"initial_capital      : {INIT:,.2f}")
print(f"sum(net_cash_delta)  : {net_deltas:,.2f}")
print(f"ledger-derived cash  : {ledger_cash:,.2f}")
print(f"state.json cash      : {cash:,.2f}")
print(f"RECONCILES           : {abs(ledger_cash - cash) < 0.01}")
print()
print("=== COST DRAG (new cost model, both sides) ===")
print(f"total trades         : {len(txns)}")
print(f"total brokerage fees : KES {total_fees:,.2f}")
print(f"total slippage       : KES {total_slip:,.2f}")
print(f"TOTAL round-trip drag: KES {total_drag:,.2f}")
print()
print("=== HOLDINGS @ AXYS OFFICIAL 4-Aug-26 ===")
for sym, sh, px, mv, cost in rows:
    print(f"  {sym:5s} {sh:>5d} @ {px:>8.2f} = {mv:>10,.2f}  (cost {cost:>10,.2f})")
print(f"  holdings total     : KES {holdings:,.2f}")
print(f"  stored cost basis  : KES {stored_cost:,.2f}")
print()
print("=== HONEST NET ===")
print(f"cash                 : KES {cash:,.2f}")
print(f"holdings (official)  : KES {holdings:,.2f}")
print(f"EQUITY               : KES {equity:,.2f}")
print(f"NET P&L              : KES {net:,.2f}  ({net_pct:+.2f}%)")
print()
print("=== VS QUOTED 24.75% ===")
q = INIT * 1.2475
print(f"quoted equity implied: KES {q:,.2f}")
print(f"quoted NET implied   : KES {q - INIT:,.2f}")
print(f"GAP (quoted - honest): KES {q - equity:,.2f}")
print(f"quoted%% - honest%%   : {24.75 - net_pct:+.2f} pts")
