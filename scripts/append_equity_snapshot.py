#!/usr/bin/env python3
"""Append one mark-to-market equity-curve point to snapshots.json.

Faithful replica of trading.portfolio.engine.take_snapshot's math, but with a
correct price-source per date (take_snapshot always reprices from the NEWEST
axys_closes_*.json and ignores its prices arg — unsafe for historical dates).

Price source for date D:
  - if portfolio/axys_closes_<D>.json exists -> use its 'axys' prices, tag 'axys'
  - else -> use mtm_state.json live_price, tag 'feed'
Holdings/shares/cash read from state.json (unchanged since 5 Aug).

Writes snapshots.json AND realigns benchmark.json.snapshots 1:1 with the curve
using the engine's benchmark formula (initial_capital * mean(cur/init)).
VERIFY GATE: the 4 pre-existing real benchmark values must be reproduced exactly
(within 0.01); otherwise the run aborts WITHOUT writing (no corruption).

Idempotent: skips if a snapshot for the same date already exists.
"""
import json, sys, os, datetime
from pathlib import Path

PORT = Path("/home/hermes/.trading/portfolio")
STATE = PORT / "state.json"
MTM = PORT / "mtm_state.json"
SNAP = PORT / "snapshots.json"
BENCH = PORT / "benchmark.json"
INIT_CAPITAL = 100000.0


def load(p):
    return json.load(open(p)) if p.exists() else {}


def main():
    args = sys.argv[1:]
    if "--date" in args:
        date = args[args.index("--date") + 1]
    else:
        date = datetime.date.today().isoformat()
    ts = f"{date}T19:35:00+03:00"

    snaps = load(SNAP)

    state = load(STATE)
    cash = float(state["cash"])
    positions = {p["symbol"]: p for p in state["positions"]}

    axys_f = PORT / f"axys_closes_{date}.json"
    if axys_f.exists():
        axys = json.load(open(axys_f)).get("axys", {})
        prices = {s: float(axys[s]) for s in positions if s in axys and axys[s]}
        src = "axys"
    else:
        mtm = load(MTM)
        mp = {p["symbol"]: float(p["live_price"]) for p in mtm.get("positions", [])}
        prices = {s: mp[s] for s in positions if s in mp and mp[s]}
        src = "feed"

    if not prices:
        print(f"skip {date}: no prices available")
        return 0

    holdings = round(sum(float(positions[s]["shares"]) * prices[s] for s in prices), 2)
    total = round(cash + holdings, 2)

    bench = load(BENCH)
    init = bench.get("init_prices", {})
    ratios = [prices[s] / init[s] for s in init
              if s in prices and prices[s] > 0 and init[s] > 0]
    bv = round(INIT_CAPITAL * (sum(ratios) / len(ratios)), 2) if ratios else INIT_CAPITAL

    prev = snaps[-1] if snaps else None
    dret = 0.0
    if prev:
        pt = float(prev["total_value"])
        dret = 0.0 if pt <= 0 else round((total - pt) / pt * 100, 4)
    tret = round((total - INIT_CAPITAL) / INIT_CAPITAL * 100, 4)

    point = {
        "timestamp": ts,
        "cash": round(cash, 2),
        "holdings_value": holdings,
        "total_value": total,
        "daily_return_pct": dret,
        "total_return_pct": tret,
        "drawdown_pct": 0.0,
        "benchmark_value": bv,
        "prices": {s: prices[s] for s in prices},
        "price_source": {s: src for s in prices},
    }

    # Dedup / upgrade logic (runs AFTER src/point/total are known).
    existing_idx = next((i for i, s in enumerate(snaps)
                         if s.get("timestamp", "").startswith(date)), None)
    if existing_idx is not None:
        ex = snaps[existing_idx]
        ex_src = ",".join(sorted(set(ex.get("price_source", {}).values())))
        if ex_src == "axys" and src == "axys":
            print(f"skip {date}: axys point already present")
            return 0
        if ex_src == "feed" and src == "axys":
            # UPGRADE: a 15:30 feed-sourced point is replaced once the
            # official AXYS close lands, so the curve uses ground truth.
            snaps[existing_idx] = point
            print(f"upgrade {date}: feed -> axys (total KES {total:,.2f})")
        else:
            print(f"skip {date}: point already present (src={ex_src})")
            return 0
    else:
        snaps.append(point)
    snaps.sort(key=lambda s: s["timestamp"])

    # recompute drawdown over full series (running peak)
    peak = snaps[0]["total_value"]
    for s in snaps:
        if s["total_value"] > peak:
            peak = s["total_value"]
        s["drawdown_pct"] = round((peak - s["total_value"]) / peak * 100, 4) if peak > 0 else 0.0

    # ---- VERIFY GATE: pre-existing real benchmark values must reproduce ----
    orig_bench = load(BENCH)
    orig_real = orig_bench.get("snapshots", [])[:4]  # 07-13,07-20,07-21,08-05 08:48
    for i, ob in enumerate(orig_real):
        new_v = snaps[i]["benchmark_value"]
        if abs(new_v - float(ob.get("value", 0))) > 0.01:
            print(f"ABORT: benchmark gate failed at index {i} "
                  f"(orig {ob.get('value')} vs new {new_v})")
            return 2

    # write atomically
    tmp = SNAP.with_suffix(".json.tmp")
    json.dump(snaps, open(tmp, "w"), indent=2)
    os.replace(tmp, SNAP)
    try:
        os.chmod(SNAP, 0o640)
    except OSError:
        pass

    bsnaps = [{"timestamp": s["timestamp"], "value": s["benchmark_value"]} for s in snaps]
    bench["snapshots"] = bsnaps
    tmpb = BENCH.with_suffix(".json.tmp")
    json.dump(bench, open(tmpb, "w"), indent=2)
    os.replace(tmpb, BENCH)
    try:
        os.chmod(BENCH, 0o640)
    except OSError:
        pass

    print(f"appended {date}: total KES {total:,.2f} ({tret:+.2f}%) "
          f"src={src} bench=KES {bv:,.2f} (series now {len(snaps)} pts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
