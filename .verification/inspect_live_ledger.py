"""Compute the honest live-ledger metrics per AUDIT-005 intent.
Live ledger = portfolio/transactions.json.
win_rate = profitable SELLs / total SELLs (fee-inclusive), PF = gross_win/gross_loss.
"""
import json
import sys
from collections import Counter

txns = json.load(open("/home/hermes/.trading/portfolio/transactions.json"))
if isinstance(txns, dict):
    txns = txns.get("transactions", txns.get("txns", []))
print("total txns:", len(txns), "types:", Counter(isinstance(t, dict) for t in txns))
if txns and isinstance(txns[0], dict):
    print("sample keys:", sorted(txns[0].keys())[:20])
    print("sample:", {k: txns[0].get(k) for k in list(txns[0].keys())[:10]})

sells = [t for t in txns if isinstance(t, dict) and t.get("action") == "SELL"]
print("SELL count:", len(sells))
for t in sells[:8]:
    print("  ", t.get("timestamp"), t.get("symbol"), "price", t.get("price"), "shares", t.get("shares"),
          "fee", t.get("fee"), "realised", t.get("realised_pnl"))

# Compute honest win rate from realised_pnl where present
with_pnl = [t for t in sells if t.get("realised_pnl") is not None]
print("SELLs with realised_pnl:", len(with_pnl))
wins = [t for t in with_pnl if t["realised_pnl"] > 0]
losses = [t for t in with_pnl if t["realised_pnl"] <= 0]
print(f"wins={len(wins)} losses={len(losses)} win_rate={len(wins)/len(with_pnl)*100:.1f}%" if with_pnl else "no pnl data")
gross_win = sum(t["realised_pnl"] for t in wins)
gross_loss = -sum(t["realised_pnl"] for t in losses)
print(f"gross_win={gross_win:.2f} gross_loss={gross_loss:.2f} PF={gross_win/gross_loss:.2f}" if gross_loss > 0 else f"gross_win={gross_win:.2f} no losses")
