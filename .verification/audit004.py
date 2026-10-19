"""AUDIT-004 verification: realised P&L includes fees; PaperBroker equity uses market value.
Round-trip BUY -> SELL in a sandbox portfolio must show realised == gross - 2x(fee+slip).
"""
import os
import sys
import tempfile
import json

sys.path.insert(0, os.path.expanduser("~/.trading"))
sys.path.insert(0, os.path.join(os.path.expanduser("~/.trading"), "trading"))

from trading.portfolio.engine import buy, sell, load_state
from trading.execution.brokers.paper import PaperBroker
from trading import config

# Sandbox portfolio
d = tempfile.mkdtemp(prefix="audit004_")
sandbox = os.path.join(d, "portfolio")
os.makedirs(sandbox, exist_ok=True)
with open(os.path.join(sandbox, "state.json"), "w") as f:
    json.dump({"cash": 100000.0, "positions": [], "initial_capital": 100000.0}, f)

# Round trip: BUY 100 XYZ @ 50, then SELL 100 @ 55
buy_price = 50.0
sell_price = 55.0
shares = 100
gross_buy = round(shares * buy_price, 2)
gross_sell = round(shares * sell_price, 2)

ci_buy = config.trade_cost(gross_buy, buy_price)
ci_sell = config.trade_cost(gross_sell, sell_price)
print(f"[0] buy fee={ci_buy['fee']} slip={ci_buy['slippage']}")
print(f"[0] sell fee={ci_sell['fee']} slip={ci_sell['slippage']}")

state, txn_buy = buy("XYZ", shares, buy_price, dir_path=sandbox)
print(f"[1] BUY txn: price={txn_buy.price} fee={txn_buy.fee} total={txn_buy.total} net={txn_buy.net_cash_delta}")
state, txn_sell = sell("XYZ", shares, sell_price, dir_path=sandbox)
print(f"[1] SELL txn: price={txn_sell.price} fee={txn_sell.fee} total={txn_sell.total} net={txn_sell.net_cash_delta}")
print(f"[1] realised (txn) = {txn_sell.realised_pnl}")

# Expected: gross - 2*(fee+slip)
expected = round(gross_sell - gross_buy - 2 * (ci_buy["fee"] + ci_buy["slippage"] + ci_sell["fee"] + ci_sell["slippage"]), 2)
print(f"[1] expected realised = gross_sell - gross_buy - 2*(fee+slip) = {gross_sell} - {gross_buy} - 2*{ci_buy['fee']+ci_buy['slippage']+ci_sell['fee']+ci_sell['slippage']} = {expected}")
print(f"[1] txn_sell.realised_pnl == expected? {txn_sell.realised_pnl == expected}")

# Verify equity uses market value, not cost basis
# First buy back to have a position, then price it via mtm feed
state, _ = buy("XYZ", 100, 50.0, dir_path=sandbox)
# Write mtm_state.json with a live price above cost basis
with open(os.path.join(sandbox, "mtm_state.json"), "w") as f:
    json.dump({"positions": [{"symbol": "XYZ", "live_price": 60.0}]}, f)

broker = PaperBroker(portfolio_dir=sandbox)
acct = broker.get_account()
positions = broker.get_positions()
print(f"[2] account.cash={acct.cash} equity={acct.equity}")
print(f"[2] positions: {[(p.symbol, p.quantity, p.market_value, p.cost_basis) for p in positions]}")
cost_basis_val = 5000.0  # 100 * 50
market_val = 6000.0  # 100 * 60
print(f"[2] cost basis value would be {cost_basis_val}, market value {market_val}")
print(f"[2] equity uses market value? {acct.equity >= 6000.0 + acct.cash}")

print(f"[2] get_positions market_value == 100*60 = {positions[0].market_value} (expect 6000.0)")

assert txn_sell.realised_pnl == expected, f"realised mismatch: {txn_sell.realised_pnl} != {expected}"
assert acct.equity >= 6000.0 + acct.cash - 0.01, f"equity should include market value: {acct.equity}"
assert abs(positions[0].market_value - 6000.0) < 0.01, f"position market value wrong: {positions[0].market_value}"
print("AUDIT-004: OK")
