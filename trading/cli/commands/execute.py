"""Execute trades through the safety-checked execution engine.

This is the user-facing CLI for the execution layer, which sits between
strategy decisions and actual trade execution. All trades must pass
through the safety checks defined in trading.execution.safety.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import typer

from ...execution import ExecutionEngine, SafetyEngine
from ...execution.brokers import PaperBroker
from ...execution.models import OrderRequest, OrderResult, ExecutionReport


def run(
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Main entry point for the execute command group."""
    return 0


def check_cmd(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: int = typer.Option(0, "--shares", "-n", help="Number of shares to check."),
    side: str = typer.Option("BUY", "--side", help="BUY or SELL."),
    price: Optional[float] = typer.Option(None, "--price", help="Price per share (default: live price)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Dry-run safety check for a potential trade — does NOT execute."""
    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.connect()
    
    # Get live price if not provided
    if price is None:
        price = engine.broker.get_price(symbol)
    
    request = OrderRequest(
        symbol=symbol,
        side=side.upper(),
        quantity=shares,
        price=price,
    )
    
    # Only run the safety check — do NOT execute
    portfolio_state = engine._get_portfolio_state()
    account = engine.broker.get_account()
    verdict = engine.safety.check_order(request, portfolio_state, account)
    
    if as_json:
        print(json.dumps({
            "symbol": symbol,
            "side": side,
            "shares": shares,
            "price": price,
            "total": shares * price,
            "allowed": verdict.allowed,
            "reason": verdict.reason,
            "violations": verdict.violations,
        }, indent=2))
        return 0
    
    if verdict.allowed:
        print(f"✅ Trade allowed: {verdict.reason}")
    else:
        print(f"❌ Trade blocked: {verdict.reason}")
        if verdict.violations:
            print("Violations:")
            for v in verdict.violations:
                print(f"  - {v}")
    
    return 0


def buy_cmd(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: int = typer.Option(0, "--shares", "-n", help="Number of shares to buy."),
    all: bool = typer.Option(False, "--all", help="Buy as many shares as cash allows."),
    price: Optional[float] = typer.Option(None, "--price", help="Price per share (default: live price)."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional trade reason (default: signal)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Execute a BUY through the safety layer."""
    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.connect()
    
    if price is None:
        price = engine.broker.get_price(symbol)
    
    if all:
        account = engine.broker.get_account()
        shares = int(account.cash // price)
    
    request = OrderRequest(
        symbol=symbol,
        side="BUY",
        quantity=shares,
        price=price,
        reason=reason or "",
    )
    
    report = engine.execute(request)
    
    if as_json:
        print(json.dumps({
            "success": report.success,
            "symbol": symbol,
            "shares": shares,
            "price": price,
            "total": shares * price,
            "message": report.message,
            "order": report.order.to_dict() if report.order else None,
            "safety": report.safety.to_dict() if report.safety else None,
        }, indent=2))
        return 0
    
    if report.success:
        print(f"✅ BUY executed: {shares} {symbol} @ {price:.2f}")
        if report.order:
            print(f"Order ID: {report.order.order_id}")
    else:
        print(f"❌ BUY failed: {report.message}")
        if report.safety and not report.safety.allowed:
            print(f"Safety violations: {', '.join(report.safety.violations)}")
    
    return 0


def sell_cmd(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: Optional[int] = typer.Option(None, "--shares", "-n", help="Shares to sell (omit to sell all)."),
    price: Optional[float] = typer.Option(None, "--price", help="Price per share (default: live price)."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional trade reason."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Execute a SELL through the safety layer."""
    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.connect()
    
    if price is None:
        price = engine.broker.get_price(symbol)
    
    request = OrderRequest(
        symbol=symbol,
        side="SELL",
        quantity=shares if shares is not None else 0,  # 0 means "all" in the portfolio engine
        price=price,
        reason=reason or "",
    )
    
    report = engine.execute(request)
    
    if as_json:
        print(json.dumps({
            "success": report.success,
            "symbol": symbol,
            "shares": shares,
            "price": price,
            "total": (shares or 0) * price,
            "message": report.message,
            "order": report.order.to_dict() if report.order else None,
            "safety": report.safety.to_dict() if report.safety else None,
        }, indent=2))
        return 0
    
    if report.success:
        print(f"✅ SELL executed: {shares or 'all'} {symbol} @ {price:.2f}")
        if report.order:
            print(f"Order ID: {report.order.order_id}")
    else:
        print(f"❌ SELL failed: {report.message}")
        if report.safety and not report.safety.allowed:
            print(f"Safety violations: {', '.join(report.safety.violations)}")
    
    return 0


def status_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Show execution engine status (broker + safety)."""
    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.connect()
    
    status = engine.get_status()
    
    if as_json:
        print(json.dumps(status, indent=2))
        return 0
    
    print(f"Broker: {status['broker']}")
    print(f"Connected: {'✅' if status['connected'] else '❌'}")
    
    if status['account']:
        print(f"Cash: {status['account']['cash']:.2f} KES")
        print(f"Equity: {status['account']['equity']:.2f} KES")
        print(f"Positions: {status['account']['positions_count']}")
    
    safety = status['safety']
    print(f"\nSafety Engine:")
    print(f"Enabled: {'✅' if safety['config']['enabled'] else '❌'}")
    print(f"Emergency Stop: {'🔴 ACTIVE' if safety['emergency_stop'] else '🟢 Inactive'}")
    print(f"Daily P&L: {safety['daily_realised_pnl']:.2f} KES")
    print(f"Daily Trades: {safety['daily_trade_count']}")
    print(f"Daily Gross Loss: {safety['daily_gross_loss']:.2f} KES")
    
    if safety['manual_overrides']:
        print("\nManual Overrides:")
        for sym, action in safety['manual_overrides'].items():
            print(f"  {sym}: {action}")
    
    return 0


def emergency_stop_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Activate emergency stop — no more trades."""
    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.safety.emergency_stop()
    
    if as_json:
        print(json.dumps({"status": "emergency_stop_activated"}, indent=2))
        return 0
    
    print("🔴 EMERGENCY STOP ACTIVATED - ALL TRADES BLOCKED")
    return 0


def release_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Release emergency stop — resume trading."""
    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.safety.release_emergency_stop()
    
    if as_json:
        print(json.dumps({"status": "emergency_stop_released"}, indent=2))
        return 0
    
    print("🟢 EMERGENCY STOP RELEASED - TRADING RESUMED")
    return 0


def override_cmd(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    block: bool = typer.Option(False, "--block", help="Block all trades for this symbol."),
    force: bool = typer.Option(False, "--force", help="Force allow trades for this symbol."),
    clear: bool = typer.Option(False, "--clear", help="Remove override for this symbol."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Set manual override (block/force) for a symbol."""
    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    
    if clear:
        engine.safety.clear_manual_override(symbol)
        action = "cleared"
    elif block:
        engine.safety.set_manual_override(symbol, "block")
        action = "blocked"
    elif force:
        engine.safety.set_manual_override(symbol, "force")
        action = "forced"
    else:
        raise typer.BadParameter("Must specify --block, --force, or --clear")
    
    if as_json:
        print(json.dumps({"symbol": symbol, "action": action}, indent=2))
        return 0
    
    print(f"Override {action} for {symbol}")
    return 0


def reset_daily_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Manually reset daily counters."""
    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.safety.reset_daily()
    
    if as_json:
        print(json.dumps({"status": "daily_counters_reset"}, indent=2))
        return 0
    
    print("✅ Daily counters reset")
    return 0


def plan_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Generate a trade plan from the current allocation proposal.

    Reads the allocation engine output and converts recommendations
    into concrete OrderRequests with live prices and safety verdicts.
    Does NOT execute — use ``deploy`` to act on an approved plan.
    """
    from ...allocation import generate_allocation

    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.connect()

    alloc = generate_allocation()
    ps = alloc["portfolio_summary"]
    cash = ps["cash"]
    total_value = ps["total_value"]

    # Build concrete orders from allocation actions
    orders: list[dict] = []
    for a in alloc["allocations"]:
        action = a["action"]
        if action not in ("Add", "Reduce", "Sell", "Open"):
            continue

        sym = a["symbol"]
        adjustment = a["adjustment"]
        current_price = engine.broker.get_price(sym)
        if current_price <= 0:
            continue

        if action in ("Add", "Open") and adjustment > 0:
            shares = int(adjustment / current_price)
            if shares < 1:
                continue
            side = "BUY"
            # Run safety check
            request = OrderRequest(symbol=sym, side="BUY", quantity=shares, price=current_price)
            account = engine.broker.get_account()
            portfolio_state = engine._get_portfolio_state()
            verdict = engine.safety.check_order(request, portfolio_state, account)
            orders.append({
                "symbol": sym,
                "side": side,
                "shares": shares,
                "price": current_price,
                "total": round(shares * current_price, 2),
                "reason": f"{a['tier']}: {a['holding_period']} target",
                "allowed": verdict.allowed,
                "safety_reason": verdict.reason,
                "violations": verdict.violations,
            })
        elif action in ("Reduce", "Sell") and adjustment < 0:
            shares = min(int(abs(adjustment) / current_price), int(a["current_value"] / a.get("current_value", current_price)) if a.get("current_value") else 0)
            if shares < 1:
                shares = 1  # sell at least 1 if reducing
            side = "SELL"
            request = OrderRequest(symbol=sym, side="SELL", quantity=shares, price=current_price)
            account = engine.broker.get_account()
            portfolio_state = engine._get_portfolio_state()
            verdict = engine.safety.check_order(request, portfolio_state, account)
            orders.append({
                "symbol": sym,
                "side": side,
                "shares": shares,
                "price": current_price,
                "total": round(shares * current_price, 2),
                "reason": f"{a['tier']}: reduce position",
                "allowed": verdict.allowed,
                "safety_reason": verdict.reason,
                "violations": verdict.violations,
            })

    if as_json:
        print(json.dumps({
            "generated_at": alloc["generated_at"],
            "portfolio_summary": ps,
            "plan": orders,
        }, indent=2))
        return 0

    if not orders:
        print("✅ No actionable orders — portfolio is balanced.")
        return 0

    print(f"\n  TRADE PLAN — {len(orders)} order(s)")
    print(f"{'='*60}")
    print(f"  Portfolio: KES {total_value:,.2f}  |  Cash: KES {cash:,.2f}")
    print()
    print(f"  {'SYMBOL':<7} {'SIDE':<5} {'SHARES':>7} {'PRICE':>8} {'TOTAL':>10} {'SAFETY':<9} REASON")
    print(f"  {'─'*60}")
    for o in orders:
        safety_icon = "✅" if o["allowed"] else "❌"
        print(f"  {o['symbol']:<7} {o['side']:<5} {o['shares']:>7} {o['price']:>8.2f} {o['total']:>10,.0f} {safety_icon:<9} {o['reason'][:30]}")
        if not o["allowed"] and o["violations"]:
            for v in o["violations"]:
                print(f"  {'':<7} {'':<5} {'':>7} {'':>8} {'':>10} {'':<9} ⚠️  {v}")
    print()
    blocked = [o for o in orders if not o["allowed"]]
    passed = [o for o in orders if o["allowed"]]
    if blocked:
        print(f"  ⚠️  {len(blocked)} order(s) blocked by safety — run `trading execute override` if intentional")
    if passed:
        print(f"  ✅ {len(passed)} order(s) cleared — run `trading execute deploy` to execute")
    return 0


def deploy_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Execute the current trade plan from the allocation proposal.

    Runs all safety-cleared orders through the execution engine.
    Re-checks safety before each trade (state may have changed).
    """
    from ...allocation import generate_allocation

    engine = ExecutionEngine(PaperBroker(), SafetyEngine())
    engine.connect()

    alloc = generate_allocation()
    ps = alloc["portfolio_summary"]

    results: list[dict] = []
    for a in alloc["allocations"]:
        action = a["action"]
        if action not in ("Add", "Reduce", "Sell", "Open"):
            continue

        sym = a["symbol"]
        adjustment = a["adjustment"]
        current_price = engine.broker.get_price(sym)
        if current_price <= 0:
            continue

        if action in ("Add", "Open") and adjustment > 0:
            shares = int(adjustment / current_price)
            if shares < 1:
                continue
            request = OrderRequest(
                symbol=sym,
                side="BUY",
                quantity=shares,
                price=current_price,
                reason=f"{a['tier']}: allocation target",
            )
            report = engine.execute(request)
            results.append({
                "symbol": sym,
                "side": "BUY",
                "shares": shares,
                "price": current_price,
                "success": report.success,
                "message": report.message,
            })
        elif action in ("Reduce", "Sell") and adjustment < 0:
            shares = min(int(abs(adjustment) / current_price), int(a["current_value"] / current_price) if a.get("current_value") else 1)
            if shares < 1:
                shares = 1
            request = OrderRequest(
                symbol=sym,
                side="SELL",
                quantity=shares,
                price=current_price,
                reason=f"{a['tier']}: reduce position",
            )
            report = engine.execute(request)
            results.append({
                "symbol": sym,
                "side": "SELL",
                "shares": shares,
                "price": current_price,
                "success": report.success,
                "message": report.message,
            })

    if as_json:
        print(json.dumps({"results": results}, indent=2))
        return 0

    if not results:
        print("✅ No trades to execute — portfolio is balanced.")
        return 0

    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count

    print(f"\n  DEPLOY RESULTS")
    print(f"{'='*60}")
    for r in results:
        icon = "✅" if r["success"] else "❌"
        print(f"  {icon} {r['side']} {r['shares']:>4} {r['symbol']:<6s} @ {r['price']:>8.2f} — {r['message']}")
    print()
    if fail_count > 0:
        print(f"  ⚠️  {fail_count} trade(s) failed — check safety/portfolio state")
    else:
        print(f"  ✅ All {success_count} trade(s) executed successfully")
    return 0