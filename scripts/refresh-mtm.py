#!/usr/bin/env python3
"""Refresh MTM prices in portfolio state.json from latest NSE CSV data.

Reads:  ~/.trading/data/nse_*.csv  (per-stock price history)
Writes: ~/.trading/portfolio/state.json  (refreshed current_value)

Run this after market close or before auto-trader execution.
"""

import csv
import json
import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))

def get_latest_price(symbol: str) -> float | None:
    """Read the last row of nse_{symbol}.csv and return the close price."""
    csv_path = HOME / ".trading" / "data" / f"nse_{symbol}.csv"
    if not csv_path.exists():
        return None
    try:
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return None
        last = rows[-1]
        close = last.get("close") or last.get("Close")
        return float(close) if close else None
    except (ValueError, KeyError, OSError):
        return None

def get_mtm_price(symbol: str) -> float | None:
    """Read live_price from mtm_state.json for a symbol."""
    mtm_path = HOME / ".trading" / "portfolio" / "mtm_state.json"
    if not mtm_path.exists():
        return None
    try:
        with open(mtm_path) as f:
            mtm = json.load(f)
        for p in mtm.get("positions", []):
            if p["symbol"] == symbol:
                return p.get("live_price")
    except (json.JSONDecodeError, OSError):
        pass
    return None

def main():
    state_path = HOME / ".trading" / "portfolio" / "state.json"
    if not state_path.exists():
        print("❌ state.json not found")
        return 1

    with open(state_path) as f:
        state = json.load(f)

    positions = state.get("positions", [])
    if not positions:
        print("ℹ️  No positions to refresh")
        return 0

    updated = 0
    for p in positions:
        sym = p["symbol"]
        shares = p["shares"]
        avg_cost = p["avg_cost"]

        # Prefer MTM live price, fall back to CSV close
        price = get_mtm_price(sym)
        if price is None:
            price = get_latest_price(sym)
        if price is None or price <= 0:
            print(f"  ⚠️  {sym}: no price data, skipping")
            continue

        new_value = round(shares * price, 2)
        old_value = p.get("current_value", 0)
        if abs(new_value - old_value) > 0.01:
            p["current_value"] = new_value
            change = (price - avg_cost) / avg_cost * 100
            print(f"  ✅ {sym}: KES {old_value:>8,.2f} → KES {new_value:>8,.2f}  ({change:+.2f}%)")
            updated += 1

    if updated:
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)
        print(f"\n✅ Updated {updated}/{len(positions)} positions in state.json")
    else:
        print(f"ℹ️  All {len(positions)} positions already current")

    # Also cash balance check
    cash = state.get("cash", 0)
    invested = sum(p.get("current_value", 0) for p in positions)
    total = cash + invested
    initial = state.get("initial_capital", 100000)
    ret = (total - initial) / initial * 100
    print(f"\n📊 Portfolio: KES {total:>8,.2f}  ({ret:+.2f}%)  cash={cash:>8,.2f}  invested={invested:>8,.2f}")
    return 0

if __name__ == "__main__":
    main()
