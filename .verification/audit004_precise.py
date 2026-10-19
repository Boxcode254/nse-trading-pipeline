"""AUDIT-004 precise accounting analysis.

The audit finding: "realised = (effective_sell_price - avg_cost) * shares — fee
(1.5%+KES60) never deducted from realised P&L (slippage IS in effective price)."

Intent: realised P&L must reflect the TRUE round-trip P&L including fees.
We check what the fix does and what the correct value should be.
"""
import os, sys, tempfile, json
sys.path.insert(0, "/home/hermes/.trading")
from trading.portfolio.engine import buy, sell
from trading import config

def fresh():
    d = tempfile.mkdtemp(prefix="audit004precise_")
    sandbox = os.path.join(d, "portfolio")
    os.makedirs(sandbox, exist_ok=True)
    with open(os.path.join(sandbox, "state.json"), "w") as f:
        json.dump({"cash": 100000.0, "positions": [], "initial_capital": 100000.0}, f)
    return sandbox

# Same-price round trip 100 @ 50: true round-trip P&L should be -(buy_fee+buy_slip+sell_fee+sell_slip)
s = fresh()
state, tb = buy("XYZ", 100, 50.0, dir_path=s)
state, ts = sell("XYZ", 100, 50.0, dir_path=s)
cb = config.trade_cost(100 * 50.0, 50.0)
cs = config.trade_cost(100 * 50.0, 50.0)
true_cost = round(cb["fee"] + cb["slippage"] + cs["fee"] + cs["slippage"], 2)
print("Same-price round trip 100@50:")
print(f"  buy: eff={tb.price} fee={tb.fee} slip={cb['slippage']}")
print(f"  sell: eff={ts.price} fee={ts.fee} slip={cs['slippage']}")
print(f"  realised (stored) = {ts.realised_pnl}")
print(f"  true round-trip cost = {true_cost} -> honest realised should be {-true_cost}")
print(f"  MATCH? {ts.realised_pnl == -true_cost}")

# The formula in code: realised = (eff_sell - avg_cost)*shares - sell_fee
# avg_cost = buy eff price (includes buy slip). eff_sell excludes sell slip.
# So realised = (sell_px - buy_px - buy_slip - sell_slip)*shares - sell_fee
# which = (sell_px-buy_px)*shares - (buy_slip+sell_slip)*shares - sell_fee
# and buy_fee is NOT included anywhere.
formula_result = round((ts.price - tb.price) * 100 - ts.fee, 2)
print(f"  formula (eff_sell - avg_cost)*100 - sell_fee = {formula_result}")
print(f"  buy_fee={cb['fee']} is absent from the realised computation")
