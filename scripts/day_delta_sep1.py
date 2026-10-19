#!/usr/bin/env python3
"""Day-delta for 1 Sep 2026 Market Watch: per-symbol MV impact vs 31 Aug closes file."""
import json

PORT = "/home/hermes/.trading/portfolio/"
prev = json.load(open(PORT + "axys_closes_2026-08-31.json"))["axys"]
new = json.load(open(PORT + "axys_closes_2026-09-01.json"))["axys"]
mtm = json.load(open(PORT + "mtm_state.json"))
cash = mtm["cash"]

rows = []
total_delta = 0.0
mv_prev = 0.0
mv_new = 0.0
for pos in mtm["positions"]:
    s = pos["symbol"]; sh = pos["shares"]
    p0, p1 = prev[s], new[s]
    d = sh * (p1 - p0)
    total_delta += d
    mv_prev += sh * p0
    mv_new += sh * p1
    rows.append((s, sh, p0, p1, (p1 - p0) / p0 * 100, d))

rows.sort(key=lambda r: -abs(r[5]))
print(f"{'SYM':<6}{'shares':>8}{'prev':>9}{'new':>9}{'chg%':>8}{'MV delta KES':>14}")
for s, sh, p0, p1, chg, d in rows:
    print(f"{s:<6}{sh:>8}{p0:>9.2f}{p1:>9.2f}{chg:>7.2f}%{d:>+14.2f}")
print()
print(f"MV prev: {mv_prev:,.2f}  MV new: {mv_new:,.2f}")
print(f"Total delta: {total_delta:+,.2f}  ({total_delta / (mv_prev + cash) * 100:+.2f}% of equity)")
print(f"Cash: {cash:,.2f}  Total now: {mv_new + cash:,.2f}  Prev total: {mv_prev + cash:,.2f}")
