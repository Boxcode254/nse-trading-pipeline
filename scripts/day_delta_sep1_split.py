#!/usr/bin/env python3
"""Split the Fri28->Tue01 delta into Fri->Mon (inferred from today's scorecard daily-chg) and Mon->Tue (verified tape)."""
import json

PORT = "/home/hermes/.trading/portfolio/"
fri = json.load(open(PORT + "axys_closes_2026-08-31.json"))["axys"]  # as-of 28 Aug (Weekly Note scorecard)
tue = json.load(open(PORT + "axys_closes_2026-09-01.json"))["axys"]  # verified 1 Sep close
mtm = json.load(open(PORT + "mtm_state.json"))

# Today's Market Pulse scorecard daily change (Mon 31 Aug -> Tue 1 Sep session), percent
tape_chg = {"ABSA": -0.1, "COOP": 0.3, "EABL": 0.4, "SCOM": 0.1, "KCB": 1.3,
            "SCBK": 0.0, "TOTL": 0.9, "KNRE": 6.3, "EQTY": 2.4, "KPLC": 4.0}

print(f"{'SYM':<6}{'fri':>9}{'mon(inf)':>10}{'tue':>9}{'fri->mon%':>11}{'mon->tue%':>11}")
tot_fm = tot_mt = 0.0
for pos in mtm["positions"]:
    s = pos["symbol"]; sh = pos["shares"]
    p_fri, p_tue = fri[s], tue[s]
    mon_inf = round(p_tue / (1 + tape_chg[s] / 100), 2)  # inferred Monday close
    fm = sh * (mon_inf - p_fri)
    mt = sh * (p_tue - mon_inf)
    tot_fm += fm; tot_mt += mt
    print(f"{s:<6}{p_fri:>9.2f}{mon_inf:>10.2f}{p_tue:>9.2f}{(mon_inf-p_fri)/p_fri*100:>10.2f}%{(p_tue-mon_inf)/mon_inf*100:>10.2f}%")
print(f"\nFri->Mon (INFERRED):  {tot_fm:+,.2f} KES")
print(f"Mon->Tue (tape):      {tot_mt:+,.2f} KES")
print(f"Total:                {tot_fm+tot_mt:+,.2f} KES")
