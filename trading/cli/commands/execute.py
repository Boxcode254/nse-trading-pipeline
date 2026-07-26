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
from ...execution.order_store import OrderStore


def run(
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Main entry point for the execute command group."""
    return 0


def _get_execution_engine(production: bool = False) -> ExecutionEngine:
    """Get a shared ExecutionEngine instance with order store and alerts path.

    ``production`` must be True to permit writes to the live portfolio. The
    engine refuses to mutate prod otherwise (production-write guard).
    """
    order_store = OrderStore(store_dir=os.path.expanduser("~/.trading/execution/orders"))
    alerts_path = os.path.expanduser("~/.trading/execution/alerts.log")
    return ExecutionEngine(
        broker=PaperBroker(),
        safety=SafetyEngine(),
        order_store=order_store,
        alerts_path=alerts_path,
        broker_timeout=10.0,
        max_retries=2,
        production=production,
    )


def check_cmd(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: int = typer.Option(0, "--shares", "-n", help="Number of shares to check."),
    side: str = typer.Option("BUY", "--side", help="BUY or SELL."),
    price: Optional[float] = typer.Option(None, "--price", help="Price per share (default: live price)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Dry-run safety check for a potential trade — does NOT execute."""
    engine = _get_execution_engine()
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
    production: bool = typer.Option(False, "--production", help="WRITE to the LIVE portfolio. Off by default: without this the engine refuses to mutate prod (use a sandbox)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Execute a BUY through the safety layer."""
    engine = _get_execution_engine(production=production)
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
    production: bool = typer.Option(False, "--production", help="WRITE to the LIVE portfolio. Off by default: without this the engine refuses to mutate prod (use a sandbox)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Execute a SELL through the safety layer."""
    engine = _get_execution_engine(production=production)
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
    engine = _get_execution_engine()
    engine.connect()

    status = engine.get_status()

    if as_json:
        print(json.dumps(status, indent=2))
        return 0

    print(f"Broker: {status['broker']}")
    print(f"Connected: {'✅' if status['connected'] else '❌'}")

    if status["account"]:
        print(f"Cash: {status['account']['cash']:.2f} KES")
        print(f"Equity: {status['account']['equity']:.2f} KES")
        print(f"Positions: {status['account']['positions_count']}")

    safety = status["safety"]
    print(f"\nSafety Engine:")
    print(f"Enabled: {'✅' if safety['config']['enabled'] else '❌'}")
    print(f"Emergency Stop: {'🔴 ACTIVE' if safety['emergency_stop'] else '🟢 Inactive'}")
    print(f"Daily P&L: {safety['daily_realised_pnl']:.2f} KES")
    print(f"Daily Trades: {safety['daily_trade_count']}")
    print(f"Daily Gross Loss: {safety['daily_gross_loss']:.2f} KES")

    if safety["manual_overrides"]:
        print("\nManual Overrides:")
        for sym, action in safety["manual_overrides"].items():
            print(f"  {sym}: {action}")

    return 0


def emergency_stop_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Activate emergency stop — no more trades."""
    engine = _get_execution_engine()
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
    engine = _get_execution_engine()
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
    engine = _get_execution_engine()

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
    as_json: Optional[bool] = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Manually reset daily counters."""
    engine = _get_execution_engine()
    engine.safety.reset_daily()

    if as_json:
        print(json.dumps({"status": "daily_counters_reset"}, indent=2))
        return 0

    print("✅ Daily counters reset")
    return 0


# ── Phase 1: Drawdown halt ───────────────────────────────────────────────
def drawdown_status_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Show the portfolio drawdown halt state."""
    engine = _get_execution_engine()
    engine.connect()
    st = engine.safety.get_status()
    out = {
        "drawdown_pct": st.get("drawdown_pct", 0.0),
        "halted": st.get("drawdown_halted", False),
        "limit": st.get("drawdown_halt_limit", 0.0),
        "reason": st.get("drawdown_halt_reason", ""),
    }
    if as_json:
        print(json.dumps(out, indent=2))
        return 0
    flag = "🔴 HALTED" if out["halted"] else "🟢 Active"
    print(f"Drawdown: {out['drawdown_pct']:.2f}%  (limit {out['limit']:.2f}%)")
    print(f"State: {flag}")
    if out["halted"]:
        print(f"Reason: {out['reason']}")
    return 0


def drawdown_release_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Release the drawdown halt (operator acknowledgement)."""
    engine = _get_execution_engine()
    engine.safety.release_drawdown_halt()
    if as_json:
        print(json.dumps({"status": "drawdown_halt_released"}, indent=2))
        return 0
    print("🟢 Drawdown halt RELEASED — trading resumes (halt re-engages if DD recurs)")
    return 0


def drawdown_sync_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> int:
    """Sync the live MTM equity-curve drawdown into the safety gate."""
    engine = _get_execution_engine()
    engine.connect()
    status = engine.sync_risk_state()
    if as_json:
        print(json.dumps(status, indent=2))
        return 0
    flag = "🔴 HALTED" if status["halted"] else "🟢 Active"
    print(f"Drawdown: {status['drawdown_pct']:.2f}%  (limit {status['limit']:.2f}%)")
    print(f"State: {flag}")
    if status["halted"]:
        print(f"Reason: {status['reason']}")
    return 0