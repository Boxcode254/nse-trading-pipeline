"""Deep-dive AUDIT-005: does _compute_win_metrics on the LIVE ledger give honest
numbers? And is the audit's claimed 8/22=36% even accurate for the current ledger?
"""
import json
import sys
from collections import Counter

sys.path.insert(0, "/home/hermes/.trading")
from trading.backtest.live_strategy_backtest import Trade, _compute_win_metrics

txns = json.load(open("/home/hermes/.trading/portfolio/transactions.json"))
if isinstance(txns, dict):
    txns = txns.get("transactions", txns.get("txns", []))

# Rebuild trades from the ledger
trades = []
for t in txns:
    if not isinstance(t, dict):
        continue
    side = "BUY" if t.get("action") == "BUY" else "SELL"
    shares = t.get("shares", 0)
    price = t.get("price", 0.0)
    gross = round(shares * price, 2)
    trades.append(Trade(
        date=str(t.get("timestamp", ""))[:10],
        symbol=t.get("symbol", ""),
        side=side,
        shares=shares,
        price=price,
        gross=gross,
        fee=float(t.get("fee", 0.0)),
        slippage=0.0,
        net_cash=float(t.get("net_cash_delta", 0.0)),
        reason=t.get("reason", ""),
    ))

print("total trades from ledger:", len(trades))
print("side counts:", Counter(t.side for t in trades))
wr, pf = _compute_win_metrics(trades, [], [], [])
print(f"_compute_win_metrics on LIVE ledger: win_rate={wr:.1f}% PF={pf}")

# Now: the audit says 'real ledger shows 8/22 sells profitable (36%), sum realised +2158, 14 losers'
# Count from CURRENT ledger
sells = [t for t in txns if isinstance(t, dict) and t.get("action") == "SELL"]
wins = [t for t in sells if (t.get("realised_pnl") or 0) > 0]
losses = [t for t in sells if (t.get("realised_pnl") or 0) <= 0]
print(f"\nCURRENT live ledger: {len(sells)} SELLs, {len(wins)} wins, {len(losses)} losses, win_rate={len(wins)/len(sells)*100:.1f}%")
print(f"  sum realised wins={sum(t['realised_pnl'] for t in wins):.2f}, sum losses={sum(t['realised_pnl'] for t in losses):.2f}")

# Check: were the audit's 8/22 numbers from a different time? transactions may have grown.
# Verify fee-inclusive basis: are the realised_pnl stored values fee-inclusive already?
print("\n--- Sample of loss SELLs (should show why win rate is 72.7% not 36%):")
for t in losses[:6]:
    print("  ", t.get("timestamp"), t.get("symbol"), "price", t.get("price"), "realised", t.get("realised_pnl"))
