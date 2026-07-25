"""``trading target`` — Show target allocation strategy and sector analysis.

Displays the strategic sector-based target allocation model, current
portfolio weights vs targets, and generates rebalance plans.

Supports --json for machine output.

Usage:
    trading target                        Show strategy + current vs target
    trading target --json                 Machine-readable output
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .. import output

# Ensure project root
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading.target_allocation import (
    get_strategy,
    compute_sector_weights,
    compute_targets,
    get_target_allocations,
)


def run(
    quiet: bool = False,
    as_json: bool = False,
    show_rebalance: bool = False,
    dry_run: bool = True,
    verify: bool = False,
) -> int:
    """Show target allocation strategy and sector analysis.

    Args:
        quiet: Suppress human output, emit JSON.
        as_json: Emit JSON document.
        show_rebalance: Show rebalance plan.
        dry_run: Preview only (default True).
        verify: Run the engine-agreement gate (target_allocation vs decision).

    Returns:
        0 = success
    """
    try:
        from trading.target_allocation import verify_target_agreement

        if verify:
            rep = verify_target_agreement()
            if as_json or quiet:
                print(output.json_dumps(rep))
            else:
                _print_verify(rep)
            return 0

        strategy = get_strategy()
        weights = compute_sector_weights()
        targets = compute_targets(weights)
        allocs = get_target_allocations()

        if as_json or quiet:
            payload: dict[str, Any] = {
                "strategy": strategy,
                "targets": targets,
                "stock_allocations": allocs,
            }
            if show_rebalance:
                from trading.target_allocation import generate_rebalance_plan
                payload["rebalance_plan"] = generate_rebalance_plan(dry_run=dry_run)
            print(output.json_dumps(payload))
            return 0

        # Human-readable output
        _print_strategy(strategy)
        _print_targets(targets)
        _print_stock_allocations(allocs)

        if show_rebalance:
            from trading.target_allocation import generate_rebalance_plan
            plan = generate_rebalance_plan(dry_run=dry_run)
            _print_rebalance_plan(plan)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


def _print_strategy(strategy: dict[str, Any]) -> None:
    """Print the target strategy table."""
    print()
    print("  Target Allocation Strategy")
    print(f"  {'=' * 60}")
    print(f"  {'SECTOR':<18} {'TARGET':>7} {'TOLERANCE':>11}  RATIONALE")
    print(f"  {'-' * 60}")
    for sec, cfg in strategy.items():
        print(
            f"  {sec:<18} {cfg['target_pct']:>5.0f}%  ±{cfg['tolerance']:<5.0f}%  "
            f"{cfg['rationale'][:50]}"
        )
    print()


def _print_targets(targets: dict[str, Any]) -> None:
    """Print current vs target sector analysis."""
    current = targets["current"]
    cash_data = targets["cash"]

    print(f"  Sector Analysis — Portfolio: KES {targets['total_value']:,.0f}")
    print(f"  {'=' * 60}")
    print(f"  {'SECTOR':<15} {'CURRENT':>8} {'TARGET':>8} {'DRIFT':>8}  {'ACTION'}")
    print(f"  {'-' * 60}")

    for sec in sorted(current.keys()):
        info = current[sec]
        drift = info["drift_pct"]
        drift_s = f"{drift:+.1f}%"
        action_labels = {"add": "➕ Add", "trim": "➖ Trim", "hold": "⏸️  Hold"}
        action_label = action_labels.get(info["action"], info["action"])
        print(
            f"  {sec:<15} {info['current_pct']:>7.1f}% {info['target_pct']:>6.0f}%  "
            f"{drift_s:>8}  {action_label}"
        )

    # Cash line
    cash_d = cash_data["drift_pct"]
    cash_labels = {"deploy": "💰 Deploy", "raise": "🏦 Raise", "hold": "⏸️  Hold"}
    cash_act = cash_labels.get(cash_data["action"], cash_data["action"])
    print(
        f"  {'──────────────────────────────────────────────────────────────────':>50}"
    )
    print(
        f"  {'cash':<15} {cash_data['current_pct']:>7.1f}% {cash_data['target_pct']:>6.0f}%  "
        f"{cash_d:+.1f}%  {cash_act}"
    )

    # Summary
    s = targets["summary"]
    print()
    print(
        f"  Sectors: {s['on_target']} on target · {s['within_tolerance']} within tolerance · "
        f"{s['over_weight']} over · {s['under_weight']} under"
    )


def _print_stock_allocations(allocs: dict[str, float]) -> None:
    """Print per-stock allocation targets."""
    if not allocs:
        return
    print()
    print("  Per-Stock Allocation Targets")
    print(f"  {'=' * 40}")
    for sym, pct in sorted(allocs.items()):
        sec = _sector_of(sym)
        print(f"    {sym:<6s} → {pct:>5.1f}%  ({sec})")


def _print_rebalance_plan(plan: dict[str, Any]) -> None:
    """Print the rebalance trade plan."""
    trades = plan["trades"]
    summary = plan["summary"]

    if not trades:
        print()
        print("  Rebalance Plan: ✅ No trades needed — portfolio is balanced.")
        return

    print()
    mode = " (DRY RUN)" if summary["dry_run"] else ""
    print(f"  Rebalance Plan{mode}")
    print(f"  {'=' * 60}")
    for t in trades:
        emoji = "🟢" if t["side"] == "BUY" else "🔴"
        print(
            f"  {emoji} {t['side']:4s} {t['shares']:>4d} {t['symbol']:<6s}  "
            f"@ KES {t['price']:>7.2f} = KES {t['value']:>8,.0f}"
        )
        print(f"      {t['reason']}")
    print(f"  {'-' * 60}")
    print(f"  Buy:  KES {summary['total_buy_value']:>8,.0f}")
    print(f"  Sell: KES {summary['total_sell_value']:>8,.0f}")
    print(f"  Net:  KES {summary['net_cash']:>+8,.0f}")
    print(f"  Trades: {summary['trade_count']}")


def _sector_of(symbol: str) -> str:
    """Quick sector lookup."""
    from trading.target_allocation import SECTOR_MAP
    return SECTOR_MAP.get(symbol, "other")


def _print_verify(rep: dict[str, Any]) -> None:
    """Print the engine-agreement gate result."""
    print()
    print("  Engine Target-Agreement Gate")
    print(f"  {'=' * 60}")
    flag = "✅ AGREE" if rep["agreed"] else "❌ DIVERGE"
    verified = rep.get("verified", True)
    mode = "nse_only" if rep.get("nse_only") else "multi-asset"
    print(f"  Mode:        {mode}")
    print(f"  Status:      {flag}")
    print(f"  Max diff:    {rep['max_abs_diff']:.2f}%   (tolerance {rep['tolerance']:.1f}%)")
    if not verified:
        print("  ⚠️  Decision Engine unreachable — verification inconclusive (fail-open).")
    print()
    print(f"  {'SYMBOL':<7} {'TARGET':>9} {'DECISION':>10} {'DIFF':>8}")
    print(f"  {'-' * 38}")
    for sym, row in sorted(rep["per_stock"].items()):
        dec = "—" if row["decision"] is None else f"{row['decision']:.2f}%"
        diff = "—" if row["diff"] is None else f"{row['diff']:+.2f}%"
        print(f"  {sym:<7} {row['target_allocation']:>8.2f}% {dec:>10} {diff:>8}")

