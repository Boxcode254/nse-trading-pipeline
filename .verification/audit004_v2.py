"""AUDIT-004 verification (v2): deep-dive the fee/realised accounting.

The audit's test contract: 'round-trip BUY->SELL -> realised == gross - 2x(fee+slip)'.
We check what the code actually does and whether that contract is met in the
canonical same-price round trip (gross=0 -> realised should be -2*(fee+slip)).
"""
import os
import sys
import tempfile
import json

sys.path.insert(0, "/home/hermes/.trading")
from trading.portfolio.engine import buy, sell
from trading import config

def fresh():
    d = tempfile.mkdtemp(prefix="audit004v2_")
    sandbox = os.path.join(d, "portfolio")
    os.makedirs(sandbox, exist_ok=True)
    with open(os.path.join(sandbox, "state.json"), "w") as f:
        json.dump({"cash": 100000.0, "positions": [], "initial_capital": 100000.0}, f)
    return sandbox

# Case 1: same-price round trip 100 @ 50. gross P&L = 0. Realised should be -2*(fee+slip).
s = fresh()
state, tb = buy("XYZ", 100, 50.0, dir_path=s)
state, ts = sell("XYZ", 100, 50.0, dir_path=s)
ci_b = config.trade_cost(100 * 50.0, 50.0)
ci_s = config.trade_cost(100 * 50.0, 50.0)
print("CASE1 same-price:")
print("  buy fee=%s slip=%s eff_price=%s" % (ci_b["fee"], ci_b["slippage"], tb.price))
print("  sell fee=%s slip=%s eff_price=%s" % (ci_s["fee"], ci_s["slippage"], ts.price))
print("  realised =", ts.realised_pnl)
print("  2*(fee+slip) =", round(2 * (ci_b["fee"] + ci_b["slippage"]), 2))
print("  realised == -2*(fee+slip)?", ts.realised_pnl == -round(2 * (ci_b["fee"] + ci_b["slippage"]), 2))

# Case 2: the actual round trip at different prices (as my v1 script)
s = fresh()
state, tb = buy("XYZ", 100, 50.0, dir_path=s)
state, ts = sell("XYZ", 100, 55.0, dir_path=s)
print("CASE2 buy@50 sell@55:")
print("  buy eff=%s (fee %s slip %s) sell eff=%s (fee %s slip %s)" % (
    tb.price, tb.fee, ci_b["slippage"], ts.price, ts.fee, ci_s["slippage"]))
print("  realised =", ts.realised_pnl)
# What SHOULD it be? realised = (sell_eff - buy_eff) * shares - sell_fee
# but buy_eff includes BUY slippage (paid), sell_eff excludes sell slippage (received less).
# The full all-in cash round trip: net_cash_delta(buy) + net_cash_delta(sell)
buy_net = tb.net_cash_delta
sell_net = ts.net_cash_delta
print("  net cash round trip = %s + %s = %s" % (buy_net, sell_net, round(buy_net + sell_net, 2)))
# Now what does the realised formula produce: (eff_sell - avg_cost)*shares - fee
# avg_cost = buy eff price (50.075)
avg_cost = tb.price
eff_sell = ts.price
realised_calc = round((eff_sell - avg_cost) * 100 - ts.fee, 2)
print("  formula (eff_sell - avg_cost)*shares - fee =", realised_calc)
print("  Does realised include BUY fee? Compare with net-cash round trip:")
print("  net round trip =", round(buy_net + sell_net, 2), " vs realised =", ts.realised_pnl)
