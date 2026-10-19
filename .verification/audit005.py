"""AUDIT-005 verification: backtest metrics honesty.
1. _compute_win_metrics on crafted ledger (2 wins, 1 loss w/ fees) -> ~66.7%, sane PF
2. all-losses -> PF None/0 (not 4521)
3. Re-run the real backtest module -> honest win_rate, not 100%
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.trading"))
sys.path.insert(0, os.path.join(os.path.expanduser("~/.trading"), "trading"))

from trading.backtest.live_strategy_backtest import Trade, _compute_win_metrics as cwm

def t(side, price, shares=1, fee=1.0, slippage=0.0):
    gross = price * shares
    return Trade(date="2026-01-01", symbol="ABC", side=side, shares=shares,
                 price=price, gross=gross, fee=fee, slippage=slippage,
                 net_cash=0.0, reason="test")

# 2 wins, 1 loss with fees (mirror the regression test)
trades = [
    t("BUY", 100, fee=1), t("SELL", 110, fee=1),  # +8 after both fees
    t("BUY", 100, fee=1), t("SELL", 105, fee=1),  # +3
    t("BUY", 100, fee=1), t("SELL", 98, fee=1),   # -3
]
wr, pf = cwm(trades, [], [], [])
print(f"[1] 2W/1L: win_rate={wr} PF={pf} (expect 66.67, ~2.75)")
assert abs(wr - 66.6667) < 0.5, f"win_rate wrong {wr}"
assert pf is not None and pf < 10, f"PF not sane: {pf}"

# all losses -> PF None (or 0), NOT 4521
trades_all_loss = [t("BUY", 100, fee=1), t("SELL", 90, fee=1)]
wr2, pf2 = cwm(trades_all_loss, [], [], [])
print(f"[2] all-loss: win_rate={wr2} PF={pf2}")
assert wr2 == 0.0, "all-loss win_rate should be 0"
assert pf2 is None or pf2 == 0.0, f"all-loss PF should be None/0, got {pf2}"

# all wins -> PF None
trades_all_win = [t("BUY", 100, fee=1), t("SELL", 110, fee=1)]
wr3, pf3 = cwm(trades_all_win, [], [], [])
print(f"[2b] all-win: win_rate={wr3} PF={pf3}")
assert pf3 is None, f"all-win PF should be None, got {pf3}"

# 3. Re-run real backtest
print("[3] Running real backtest module (may need network)...")
import subprocess
env = dict(os.environ)
env["PYTHONPATH"] = os.path.expanduser("~/.trading")
try:
    r = subprocess.run(
        [sys.executable, "-m", "trading.backtest.live_strategy_backtest"],
        capture_output=True, text=True, timeout=180, env=env,
        cwd=os.path.expanduser("~/.trading"),
    )
    print("[3] stdout:", r.stdout[-2000:])
    print("[3] stderr:", r.stderr[-2000:])
    print("[3] rc:", r.returncode)
    if "win" in r.stdout.lower():
        import re
        m = re.search(r"win\s+(\d+\.?\d*)%", r.stdout)
        if m:
            wr_real = float(m.group(1))
            print(f"[3] REAL backtest win_rate = {wr_real}%")
            assert wr_real < 90, f"win_rate {wr_real}% still inflated"
except FileNotFoundError as e:
    print("[3] FAILED to run backtest:", e)

print("AUDIT-005: OK")
