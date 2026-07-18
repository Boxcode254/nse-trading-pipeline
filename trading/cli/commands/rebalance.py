"""``trading rebalance`` — portfolio rebalancing with drift analysis and trade suggestions.

Compares current portfolio allocations against target weights, computes
drift, and suggests specific trades to bring the portfolio back to target.
Supports ``--apply`` to execute the suggested trades, ``--json``, and
``--output FILE``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.table import Table

from .. import output
from ... import config
from ...services import portfolio as pf_svc


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_current_positions() -> list[dict[str, Any]]:
    """Get current portfolio positions with values."""
    result = pf_svc.show()
    if result.get("status") != "ok" and result.get("status") != "not_initialised":
        return []
    # result["positions"] is a list of {symbol, shares, avg_cost, last_price, value, pnl, pnl_pct}
    return result.get("positions", [])


def _get_portfolio_summary() -> dict[str, Any]:
    """Get portfolio summary values."""
    result = pf_svc.show()
    if result.get("status") != "ok":
        return {"cash": 0, "total_value": 0, "initial_capital": 0}
    return {
        "cash": result.get("cash", 0),
        "total_value": result.get("total_value", 0),
        "initial_capital": result.get("initial_capital", 0),
    }


def _get_target_allocations(current_positions: list[dict]) -> dict[str, float]:
    """Generate target allocation percentages from the strategic sector model.

    Uses trading.target_allocation.get_target_allocations() which converts
    sector-level targets (banking 50%, telecom 15%, etc.) into per-stock
    targets. Falls back to equal-weight for stocks not covered by the
    sector model (e.g., non-NSE symbols or experimental positions).
    """
    # Primary: strategic sector-based targets
    try:
        from trading.target_allocation import get_target_allocations as _get_ta
        sector_targets = _get_ta()
        if sector_targets:
            # The result covers all strategy stocks — merge with current positions
            result: dict[str, float] = {}
            # Include all sector-based targets
            for sym, pct in sector_targets.items():
                result[sym] = pct
            # For any current position not covered by the strategy (e.g. WTK),
            # assign a residual weight split equally
            strategy_symbols = set(result.keys())
            uncovered = [p["symbol"] for p in current_positions
                         if p["symbol"] not in strategy_symbols]
            if uncovered:
                remaining_pct = 100.0 - sum(result.values())
                if remaining_pct > 0 and uncovered:
                    per_stock = remaining_pct / len(uncovered)
                    for sym in uncovered:
                        result[sym] = round(per_stock, 2)
            return result
    except Exception:
        pass

    # Fallback: equal-weight allocation (original logic)
    symbols: set[str] = set()
    for pos in current_positions:
        symbols.add(pos["symbol"])
    for pair in config.PAIRS:
        if "/" in pair:
            continue
        symbols.add(pair)
    if not symbols:
        return {}
    equal_weight = 100.0 / len(symbols)
    return {sym: round(equal_weight, 2) for sym in sorted(symbols)}


def _compute_drift(
    current_positions: list[dict],
    target_allocs: dict[str, float],
    total_value: float,
) -> list[dict[str, Any]]:
    """Compute drift between current and target allocations.

    Returns a list of dicts with symbol, current_pct, target_pct, drift,
    action, and suggested_trade (shares to buy/sell).
    """
    drift_table = []
    position_map = {p["symbol"]: p for p in current_positions}

    for symbol in sorted(target_allocs):
        target_pct = target_allocs[symbol]
        pos = position_map.get(symbol)
        current_value = pos["value"] if pos else 0.0
        current_pct = (current_value / total_value * 100) if total_value > 0 else 0.0
        drift = current_pct - target_pct

        # Determine action
        if abs(drift) < 2.0:
            action = "HOLD"
        elif drift > 2.0:
            action = "SELL"
        else:
            action = "BUY"

        # Calculate suggested trade value to reach target
        target_value = total_value * (target_pct / 100.0)
        trade_value = target_value - current_value
        last_price = pos["last_price"] if pos else 0.0

        suggested_shares = 0
        if last_price > 0 and abs(trade_value) >= last_price:
            suggested_shares = int(trade_value / last_price)
            # Don't suggest selling more shares than we have
            if pos and suggested_shares < -pos["shares"]:
                suggested_shares = -pos["shares"]

        drift_table.append({
            "symbol": symbol,
            "current_pct": round(current_pct, 2),
            "target_pct": target_pct,
            "drift": round(drift, 2),
            "action": action,
            "current_value": round(current_value, 2),
            "target_value": round(target_value, 2),
            "trade_value": round(trade_value, 2),
            "last_price": last_price,
            "suggested_shares": suggested_shares,
        })

    return drift_table


def _apply_rebalance(drift_table: list[dict]) -> list[dict[str, Any]]:
    """Execute the suggested rebalance trades via the portfolio service.

    Returns a list of execution results.
    """
    results = []
    for row in drift_table:
        shares = row["suggested_shares"]
        if shares == 0:
            continue
        try:
            if shares > 0:
                # BUY
                reason = f"rebalance: {row['symbol']} is {row['drift']:+.2f}% under target"
                result = pf_svc.buy(symbol=row["symbol"], shares=shares, reason=reason)
                results.append({"symbol": row["symbol"], "side": "BUY", "shares": shares, "status": result.get("status")})
            else:
                # SELL
                reason = f"rebalance: {row['symbol']} is {row['drift']:+.2f}% over target"
                result = pf_svc.sell(symbol=row["symbol"], shares=abs(shares), reason=reason)
                results.append({"symbol": row["symbol"], "side": "SELL", "shares": abs(shares), "status": result.get("status")})
        except Exception as e:
            results.append({"symbol": row["symbol"], "side": "BUY" if shares > 0 else "SELL", "shares": abs(shares), "status": "error", "error": str(e)})
    return results


# ── CLI Entry Point ────────────────────────────────────────────────────────

def run(
    quiet: bool = False,
    verbose: bool = False,
    as_json: bool = False,
    output_path: str | None = None,
    apply: bool = False,
) -> int:
    """Show portfolio rebalancing analysis: current vs target with drift and trade suggestions.

    Args:
        quiet: Suppress rich output, emit JSON.
        verbose: Show raw position/target data.
        as_json: Emit JSON document on stdout.
        output_path: Write output to FILE.
        apply: Execute the suggested trades.

    Returns:
        0 = success, 1 = warning, 2 = failure.
    """
    # 1. Gather data
    summary = _get_portfolio_summary()
    total_value = summary["total_value"]
    current_positions = _get_current_positions()
    target_allocs = _get_target_allocations(current_positions)

    if not target_allocs:
        payload = {
            "status": "no_targets",
            "message": "No target allocations available. Configure monitored pairs or hold positions first.",
        }
        if as_json or quiet:
            print(output.json_dumps(payload))
        else:
            print(f"⚠️  {payload['message']}")
        return 1

    # 2. Compute drift
    drift_table = _compute_drift(current_positions, target_allocs, total_value)

    # 3. Apply if requested
    apply_results = []
    if apply:
        if total_value == 0:
            msg = "No portfolio value — cannot rebalance."
            if as_json or quiet:
                print(output.json_dumps({"status": "error", "message": msg}))
            else:
                print(f"❌ {msg}")
            return 2
        apply_results = _apply_rebalance(drift_table)

    # 4. Output
    if as_json:
        payload = {
            "status": "ok",
            "portfolio_value": total_value,
            "cash": summary["cash"],
            "target_allocations": target_allocs,
            "drift_analysis": drift_table,
        }
        if apply_results:
            payload["trades_executed"] = apply_results
        print(output.json_dumps(payload))
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                f.write(output.json_dumps(payload) + "\n")
        return 0

    if quiet:
        # Compact JSON on stdout
        print(output.json_dumps({
            "status": "ok",
            "portfolio_value": total_value,
            "drift_analysis": drift_table,
        }))
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                f.write(output.json_dumps(drift_table) + "\n")
        return 0

    # 5. Rich terminal output
    console = Console()

    # Portfolio summary header
    console.print(f"\n[bold]Portfolio Value:[/] KES {total_value:,.2f}  "
                  f"[dim]Cash: KES {summary['cash']:,.2f}[/]")

    # Drift table
    has_trades = any(r["suggested_shares"] != 0 for r in drift_table)
    title = "⚖️  Rebalance — Drift Analysis"
    if has_trades:
        title += " (with trade suggestions)"

    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Symbol", style="cyan")
    table.add_column("Current %", justify="right")
    table.add_column("Target %", justify="right")
    table.add_column("Drift", justify="right")
    table.add_column("Action")
    if has_trades:
        table.add_column("Suggested", justify="right")

    for row in drift_table:
        drift = row["drift"]
        drift_style = "green" if drift > 2 else "red" if drift < -2 else "yellow"
        action = row["action"]
        action_style = "green" if action == "BUY" else "red" if action == "SELL" else "yellow"

        cols = [
            row["symbol"],
            f"{row['current_pct']:.2f}%",
            f"{row['target_pct']:.2f}%",
            f"[{drift_style}]{drift:+.2f}%[/]",
            f"[{action_style}]{action}[/]",
        ]
        if has_trades:
            shares = row["suggested_shares"]
            if shares > 0:
                cols.append(f"[green]BUY {shares}[/]")
            elif shares < 0:
                cols.append(f"[red]SELL {abs(shares)}[/]")
            else:
                cols.append("[dim]—[/]")

        table.add_row(*cols)

    console.print()
    console.print(table)

    # Summary
    max_drift = max(abs(d["drift"]) for d in drift_table) if drift_table else 0
    if max_drift > 5:
        console.print(f"\n[bold yellow]⚠  Max drift: {max_drift:.2f}% — rebalancing recommended[/]")
    else:
        console.print(f"\n[bold green]✓  Max drift: {max_drift:.2f}% — within tolerance[/]")

    # Trade suggestions
    if has_trades:
        console.print("\n[bold]Suggested trades:[/]")
        total_cost = 0
        for row in drift_table:
            shares = row["suggested_shares"]
            if shares > 0:
                cost = shares * row["last_price"]
                total_cost += cost
                console.print(f"  [green]BUY  {shares:>4d} {row['symbol']:<6s}[/]  @ KES {row['last_price']:>8.2f}  = KES {cost:>10,.2f}")
            elif shares < 0:
                proceeds = abs(shares) * row["last_price"]
                console.print(f"  [red]SELL {abs(shares):>4d} {row['symbol']:<6s}[/]  @ KES {row['last_price']:>8.2f}  = KES {proceeds:>10,.2f}")
        console.print(f"\n  [bold]Net cash required:[/] KES {total_cost:,.2f}")
        console.print(f"  [dim]Use --apply to execute these trades.[/]")

    # Apply results
    if apply_results:
        console.print("\n[bold]Trades executed:[/]")
        for r in apply_results:
            status_icon = "✅" if r["status"] == "filled" else "❌"
            console.print(f"  {status_icon} {r['side']} {r['shares']} {r['symbol']} — {r['status']}")

    # Write output file if requested
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            for row in drift_table:
                f.write(f"{row['symbol']:<8s}  "
                        f"{row['current_pct']:6.2f}%  "
                        f"{row['target_pct']:6.2f}%  "
                        f"{row['drift']:+7.2f}%  "
                        f"{row['action']:<6s}\n")
        console.print(f"\n[dim]Saved to {output_path}[/]")

    return 0