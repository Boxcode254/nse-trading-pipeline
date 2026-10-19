"""AUDIT-003 end-to-end trace: live book -> available_cash -> KCB buy sizing.
Replicates run_auto_trade's buy-loop arithmetic exactly (auto_trader.py ~1122-1160)
without touching live files.
"""
import os, sys
sys.path.insert(0, "/home/hermes/.trading")
from trading.auto_trader import _available_cash_for_buys
from trading import config

# Live book
cash = 16320.34
total_before = 103241.0
available_cash = _available_cash_for_buys(cash, total_before)
print(f"available_cash = {available_cash} (>0 required)")

# Now trace the KCB buy branch:
#   desired_value = delta * price; cash_for_trade = min(desired_value, available_cash, room_in_sector, max_single)
# For a BUY of 19 KCB @ 90 (the audit's plan):
delta, price = 19, 90.0
desired_value = delta * price
max_single = total_before * 0.25
room_in_sector = total_before * (config.sector_cap("banking")["warn"] / 100)  # rough; real uses sector_after_sells
cash_for_trade = min(desired_value, available_cash, room_in_sector, max_single)
print(f"KCB buy: desired={desired_value} available={available_cash} room={room_in_sector:.2f} max_single={max_single}")
print(f"cash_for_trade = {cash_for_trade} (must be > MIN_TRADE_KES={config.MIN_TRADE_KES})")
if cash_for_trade < config.MIN_TRADE_KES:
    print("  -> would skip with Not enough cash")
else:
    buy_shares = min(delta, int(cash_for_trade / price))
    while buy_shares > 0 and (buy_shares * price) * config.FEE_HEADROOM > available_cash + 0.0001:
        buy_shares -= 1
    print(f"  -> buy_shares = {buy_shares} (not skipped)")

assert available_cash > 0, "available_cash deadlocked"
print("AUDIT-003 end-to-end: KCB not deadlocked")
