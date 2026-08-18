#!/usr/bin/env python3
"""Refresh MTM prices in portfolio state.json.

Price authority chain (delegated to trading.price_source, shared with the
engine write path and the auto-trader read path):
    AXYS official NSE close > mtm_state.json feed > nse_<SYM>.csv cache

Reads:  ~/.trading/portfolio/axys_closes_<date>.json  (official NSE close)
        ~/.trading/portfolio/mtm_state.json           (intraday feed)
        ~/.trading/data/nse_*.csv                     (offline cache)
Writes: ~/.trading/portfolio/state.json  (current_value ONLY)

Run this after market close or before auto-trader execution.
"""

import csv
import json
import os
import sys
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

    # PRICE CHOKEPOINT (cron side).
    # Previously this script preferred mtm_state.json's live_price and fell back
    # to the CSV close, with NO awareness of the AXYS official NSE close. Since
    # axys_reconcile.py writes mtm_state.json only, whether state.json ever saw
    # the official close depended purely on which cron fired last -- the
    # cron-ordering race. Delegate to the shared resolver so this script, the
    # engine write path and the auto-trader read path all agree on one authority
    # chain: AXYS official close > mtm_state feed > CSV cache.
    sys.path.insert(0, str(HOME / ".trading"))
    from trading import price_source

    before = {
        p["symbol"]: p.get("current_value", 0)
        for p in positions
        if p.get("symbol")
    }
    res = price_source.apply_authoritative_prices(
        state, str(state_path.parent), previous=state
    )

    updated = 0
    for p in positions:
        sym = p["symbol"]
        new_value = p.get("current_value", 0)
        old_value = before.get(sym, 0)
        if abs(new_value - old_value) > 0.01:
            avg_cost = p["avg_cost"]
            shares = p["shares"] or 1
            price = new_value / shares
            change = (price - avg_cost) / avg_cost * 100 if avg_cost else 0.0
            src = res.sources.get(sym, "cost")
            print(
                f"  ✅ {sym}: KES {old_value:>8,.2f} → KES {new_value:>8,.2f}  "
                f"({change:+.2f}%) [{src}]"
            )
            updated += 1

    print(f"\n🔎 price source: {res.summary()}")
    if res.axys_stale:
        print(
            "  ⚠️  official close is past the "
            f"{price_source.STALE_MAX_DAYS}-day freshness standard"
        )

    if updated:
        # Atomic write: tmp file + os.replace, mirroring
        # trading.portfolio.engine._write_json. This needs write permission on
        # the DIRECTORY only, not on state.json itself — the file is 0640 and
        # its owner alternates between 'hermes' (manual/AXYS runs) and
        # 'trading' (the 15:30 market-close cron), so an in-place
        # open(state_path, "w") raised PermissionError whenever the previous
        # writer was the other user. Also crash-safe: a failure mid-write can
        # no longer truncate the book.
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, state_path)
        # Preserve the established permission contract (owner rw, group r).
        try:
            os.chmod(state_path, 0o640)
            parent_gid = state_path.parent.stat().st_gid
            if state_path.stat().st_gid != parent_gid:
                os.chown(state_path, -1, parent_gid)
        except OSError:
            pass  # best-effort; never fail the write on hardening
        print(f"\n✅ Updated {updated}/{len(positions)} positions in state.json")
    else:
        print(f"ℹ️  All {len(positions)} positions already current")

    # Also cash balance check
    cash = state.get("cash", 0)
    invested = sum(p.get("current_value", 0) for p in positions)
    total = cash + invested
    initial = state.get("initial_capital", 100000)
    ret = (total - initial) / initial * 100
    print(f"\n📊 Portfolio: KES {total:>8,.2f}  (total return vs KES {initial:,.0f} start: {ret:+.2f}%)  cash={cash:>8,.2f}  invested={invested:>8,.2f}")
    # Second line: equity (cost-basis) P&L from mtm_state summary. The two
    # metrics differ only by cash drag; label both so they stop looking
    # contradictory.
    try:
        with open(os.path.join(os.path.dirname(state_path), "mtm_state.json")) as mf:
            mtm_sum = json.load(mf).get("summary", {})
        upnl_pct = mtm_sum.get("total_pnl_pct")
        if upnl_pct is not None:
            print(f"   Equity (cost-basis) P&L: {upnl_pct:+.2f}% [excl. cash]   (cash drag: {upnl_pct - ret:+.2f}pp)")
    except (OSError, json.JSONDecodeError):
        pass
    return 0

if __name__ == "__main__":
    main()
