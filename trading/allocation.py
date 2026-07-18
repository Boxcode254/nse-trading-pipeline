"""Portfolio Allocation Engine.

Generates allocation proposals based on signal scores, current portfolio,
position sizing rules, and strategic targets.

Exports
-------
generate_allocation() -> dict
    Returns allocation proposal with buy/hold/reduce/sell recommendations.

CLI
---
python3 -m trading.allocation
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure trading package is importable
_TRADING_ROOT = str(Path(__file__).resolve().parent.parent)
if _TRADING_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_ROOT)

from trading import config

# ── Paths ───────────────────────────────────────────────────────────────────
PORTFOLIO_DIR = Path.home() / ".trading" / "portfolio"
MTM_PATH = PORTFOLIO_DIR / "mtm_state.json"
STATE_PATH = PORTFOLIO_DIR / "state.json"


# ── Sector classification (mirrors config.ASSET_CATEGORIES) ───────────────
SECTOR_MAP: dict[str, str] = {
    "SCOM": "telecom", "KCB": "banking", "EQTY": "banking", "EABL": "consumer",
    "ABSA": "banking", "SCBK": "banking", "COOP": "banking", "KPLC": "utilities",
    "TOTL": "energy", "KNRE": "insurance", "WTK": "services", "BAMB": "manufacturing",
    "EUR/USD": "forex", "USD/KES": "forex",
}

# Maximum percentage of portfolio in any single sector
MAX_SECTOR_EXPOSURE_PCT: float = 35.0

# ── Allocation tier labels ────────────────────────────────────────────────
TIER_LABELS: list[tuple[float, str, str]] = [
    (90, "Strong Accumulate",  "🟢"),
    (75, "Accumulate",         "🟩"),
    (50, "Hold",               "🟡"),
    (25, "Reduce",             "🟠"),
    (0,  "Avoid",              "🔴"),
]


def _classify(score: float) -> tuple[str, str]:
    """Map a score 0-100 to (label, emoji)."""
    for threshold, label, emoji in TIER_LABELS:
        if score >= threshold:
            return label, emoji
    return "Avoid", "🔴"


def _get_portfolio() -> dict[str, Any]:
    """Get latest portfolio with live prices. Falls back to cost-basis."""
    try:
        from trading.portfolio_mtm import update_portfolio
        return update_portfolio()
    except Exception:
        pass
    if MTM_PATH.exists():
        return json.loads(MTM_PATH.read_text())
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"cash": 0, "positions": [], "initial_capital": 0}


def _get_signals() -> list[dict[str, Any]]:
    """Get latest signal scores from the ranking service."""
    try:
        from trading.services import ranking
        result = ranking.build()
        return result.get("ranked", [])
    except Exception as e:
        print(f"  ⚠️  Ranking unavailable: {e}", file=sys.stderr)
        return []


def generate_allocation() -> dict[str, Any]:
    """Generate a full portfolio allocation proposal.

    Returns dict with portfolio_summary, allocations[], and strategic_targets{}.
    """
    # 1. Current portfolio
    mtm = _get_portfolio()
    cash = mtm.get("cash", 0)
    positions = mtm.get("positions", [])
    initial_capital = mtm.get("initial_capital", 0)

    total_value = cash
    for p in positions:
        total_value += p.get("current_value") or p.get("total_cost", 0)

    # 2. Signal scores
    signals = _get_signals()
    signal_map: dict[str, dict] = {}
    for s in signals:
        sym = s.get("symbol", "")
        if sym:
            signal_map[sym] = s

    # 3. Current allocation map
    current_alloc: dict[str, float] = {"__cash__": cash}
    for p in positions:
        key = p["symbol"]
        val = p.get("current_value") or p.get("total_cost", 0)
        current_alloc[key] = val

    # 4. Position sizing limits
    exec_cfg = config.EXECUTION_CONFIG
    max_single_pct = exec_cfg.get("max_single_exposure_pct", 25.0) / 100.0
    max_positions = exec_cfg.get("max_position_count", 20)

    # 5. Build allocations for all tracked equities
    all_symbols = config.get_equity_symbols()
    allocations: list[dict[str, Any]] = []

    for sym in all_symbols:
        sig = signal_map.get(sym, {})
        score = sig.get("score", 50.0)
        tier, emoji = _classify(score)
        current_val = current_alloc.get(sym, 0.0)
        current_pct = (current_val / total_value * 100) if total_value else 0

        # Determine target from tier
        has_position = current_pct > 0.5  # meaningful position

        if tier == "Strong Accumulate":
            target_pct = max_single_pct * 100
            if has_position and current_pct < target_pct:
                action = "Add"
            elif has_position:
                action = "Hold"
            else:
                action = "Open" if score >= 80 else "Watch"
        elif tier == "Accumulate":
            target_pct = min(max_single_pct * 100 * 0.7, 15.0)
            if has_position and current_pct < target_pct:
                action = "Add"
            elif has_position:
                action = "Maintain"
            else:
                action = "Open" if score >= 75 else "Watch"
        elif tier == "Hold":
            target_pct = current_pct if has_position else 0
            action = "Hold" if has_position else "Watch"
        elif tier == "Reduce":
            target_pct = current_pct * 0.5 if has_position else 0
            action = "Reduce" if has_position else "Skip"
        else:  # Avoid
            target_pct = 0
            action = "Sell" if has_position else "Skip"

        target_value = total_value * (target_pct / 100)
        adjustment = round(target_value - current_val, 2)

        # Holding period
        holding_period = config.HOLDING_PERIODS.get(tier, "12 months")

        allocations.append({
            "symbol": sym,
            "score": round(score, 1),
            "tier": f"{emoji} {tier}",
            "action": action,
            "sector": SECTOR_MAP.get(sym, "other"),
            "current_value": round(current_val, 2),
            "current_pct": round(current_pct, 2),
            "target_pct": round(target_pct, 2),
            "target_value": round(target_value, 2),
            "adjustment": round(adjustment, 2),
            "holding_period": holding_period,
        })

    # ── Sector concentration cap ──────────────────────────────────────
    # Calculate sector exposure from CURRENT portfolio, then cap any
    # sector that exceeds MAX_SECTOR_EXPOSURE_PCT by flagging over-weight
    # names for reduction.
    sector_current: dict[str, float] = {}
    for a in allocations:
        sec = a["sector"]
        sector_current[sec] = sector_current.get(sec, 0.0) + a["current_value"]

    sector_pcts = {
        sec: (val / total_value * 100) if total_value else 0
        for sec, val in sector_current.items()
    }

    # Find sectors over the limit
    over_limit_sectors = {
        sec: pct for sec, pct in sector_pcts.items()
        if pct > MAX_SECTOR_EXPOSURE_PCT
    }

    if over_limit_sectors:
        for sec, pct in over_limit_sectors.items():
            excess_pct = pct - MAX_SECTOR_EXPOSURE_PCT
            excess_val = total_value * (excess_pct / 100)

            # List over-weight names in this sector, sorted by current value descending
            sector_names = sorted(
                [a for a in allocations if a["sector"] == sec and a["current_value"] > 0],
                key=lambda x: -x["current_value"],
            )

            # We need to reduce the biggest holdings first
            remaining_excess = excess_val
            for a in sector_names:
                if remaining_excess <= 0:
                    break
                # Reduce this position proportionally
                reduce_by = min(remaining_excess, a["current_value"] * 0.5)
                new_target = max(a["current_value"] - reduce_by, 0)
                new_pct = (new_target / total_value * 100) if total_value else 0

                a["action"] = "Reduce (sector cap)"
                a["target_pct"] = round(new_pct, 2)
                a["target_value"] = round(new_target, 2)
                a["adjustment"] = round(new_target - a["current_value"], 2)
                a["sector_cap_note"] = f"{sec} at {pct:.0f}% exceeds {MAX_SECTOR_EXPOSURE_PCT:.0f}% limit"

                remaining_excess -= reduce_by

    # Sector summary for the output
    sector_summary = {
        sec: {
            "current_pct": round(pct, 1),
            "over_limit": pct > MAX_SECTOR_EXPOSURE_PCT,
            "limit_pct": MAX_SECTOR_EXPOSURE_PCT,
        }
        for sec, pct in sorted(sector_pcts.items(), key=lambda x: -x[1])
    }

    # 6. Strategic targets
    cash_pct = (cash / total_value * 100) if total_value else 0
    strategic_targets = {
        "cash_buffer": {
            "current_pct": round(cash_pct, 2),
            "target_pct": round(max(10.0, cash_pct), 2),
            "action": "Maintain" if cash_pct >= 10 else "Build cash buffer",
        },
        "tbills": {"status": "Not implemented — allocation engine pending"},
        "gold":   {"status": "Not implemented — allocation engine pending"},
    }

    # 7. Summary
    invested = total_value - cash
    num_positions = len([a for a in allocations if a["current_value"] > 0])

    # Track which sectors have over-limit flags
    sectors_over_limit = {sec for a in allocations if a.get("sector_cap_note")}

    return {
        "generated_at": __import__("time").strftime(
            "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()
        ),
        "portfolio_summary": {
            "total_value": round(total_value, 2),
            "cash": round(cash, 2),
            "invested": round(invested, 2),
            "initial_capital": round(mtm.get("initial_capital", 0), 2),
            "total_return_pct": round(
                ((total_value - mtm.get("initial_capital", 0))
                 / (mtm.get("initial_capital", 0) or 1) * 100),
                2,
            ),
            "num_positions": num_positions,
            "max_single_exposure_pct": round(max_single_pct * 100, 1),
            "max_positions": max_positions,
            "max_sector_exposure_pct": MAX_SECTOR_EXPOSURE_PCT,
        },
        "allocations": allocations,
        "sector_summary": sector_summary,
        "strategic_targets": strategic_targets,
        "sectors_over_limit": sorted(sectors_over_limit),
    }


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Portfolio allocation proposal")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    alloc = generate_allocation()
    ps = alloc["portfolio_summary"]

    if args.json:
        print(json.dumps(alloc, indent=2))
        return

    # Human-readable output
    print(f"  PORTFOLIO ALLOCATION")
    print(f"{'='*60}")
    print(f"  Total Value:  KES {ps['total_value']:>10,.2f}")
    cash_pct_display = (ps['cash']/ps['total_value']*100) if ps['total_value'] else 0
    print(f"  Cash:         KES {ps['cash']:>10,.2f}  ({cash_pct_display:.1f}%)")
    print(f"  Invested:     KES {ps['invested']:>10,.2f}")
    print(f"  Return:       {ps['total_return_pct']:+.2f}%")
    print(f"  Positions:    {ps['num_positions']} / {ps['max_positions']} max")
    print(f"  Max/position: {ps['max_single_exposure_pct']:.0f}%")
    print(f"  Max/sector:   {ps['max_sector_exposure_pct']:.0f}%")
    print()

    # Sector exposure
    sector_summary = alloc.get("sector_summary", {})
    if sector_summary:
        print(f"  {'SECTOR':<15} {'CURRENT':>8} {' LIMIT':>8} {'STATUS':>10}")
        print(f"  {'-'*41}")
        for sec, info in sector_summary.items():
            flag = "⚠️ OVER" if info["over_limit"] else "✅ OK"
            print(f"  {sec:<15} {info['current_pct']:>7.1f}% {info['limit_pct']:>7.0f}% {flag:>10}")
        print()

    # Table
    header = f"{'SYMBOL':<7} {'SCORE':>6} {'TIER':<22} {'ACTION':<18} {'CURRENT':>9} {'TARGET':>9} {'ADJUST':>9}"
    print(header)
    print("-" * len(header))
    for a in alloc["allocations"]:
        adj = a["adjustment"]
        adj_s = f"{adj:+,.0f}" if abs(adj) >= 1 else "—"
        action = a["action"]
        note = a.get("sector_cap_note", "")
        action_display = f"{action}"
        print(f"{a['symbol']:<7} {a['score']:>6.1f} {a['tier']:<22} {action_display:<18} "
              f"{a['current_value']:>9,.0f} {a['target_value']:>9,.0f} {adj_s:>9}")

    print()
    print("  Strategic:")
    for k, v in alloc["strategic_targets"].items():
        if isinstance(v, dict) and "status" in v:
            print(f"    • {k}: {v['status']}")
        else:
            print(f"    • {k}: {v.get('current_pct', 0):.1f}% → "
                  f"{v.get('target_pct', 0):.1f}% ({v.get('action', '—')})")

    # Recommended actions
    active = [a for a in alloc["allocations"]
              if a["action"] in ("Add", "Reduce", "Sell", "Open", "Reduce (sector cap)")
              and abs(a.get("adjustment", 0)) >= 1]

    # Sector warnings
    sectors_over = alloc.get("sectors_over_limit", [])
    if sectors_over:
        print()
        print("  ⚠️  Sector Concentration Warning:")
        for sec in sectors_over:
            info = sector_summary.get(sec, {})
            print(f"     {sec} is at {info.get('current_pct', 0):.0f}% "
                  f"(limit: {info.get('limit_pct', 35):.0f}%) — "
                  f"diversify across different industries")

    if active:
        print()
        print("  Recommended:")
        for a in active:
            if a["action"] == "Open":
                print(f"    • OPEN {a['symbol']} — start position ({a['tier']}, {a['holding_period']})")
            elif a["action"] == "Reduce (sector cap)":
                note = a.get("sector_cap_note", "reduce sector concentration")
                print(f"    • SELL {a['symbol']} ({abs(a['adjustment']):,.0f} KES) — {note}")
            elif a["adjustment"] > 0:
                print(f"    • BUY {a['symbol']} ({a['adjustment']:,.0f} KES) — {a['tier']}")
            else:
                print(f"    • SELL {a['symbol']} ({abs(a['adjustment']):,.0f} KES) — {a['tier']}")
    else:
        print()
        print("  ✅ No changes needed — hold current positions.")


if __name__ == "__main__":
    main()
