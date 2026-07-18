"""Portfolio service — thin facade over the engine for cross-interface reuse.

The dashboard, future REST endpoints, and the CLI all call these
service functions. They wrap the engine in plain dicts so callers can
format freely.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ..portfolio import engine as pf


def init(capital: float = pf.DEFAULT_CAPITAL, force: bool = False) -> dict[str, Any]:
    """Create or reset the paper portfolio."""
    state = pf.init_portfolio(capital=capital, force=force)
    return {
        "status": "initialised" if not force else "reset",
        "capital": state.initial_capital,
        "cash": state.cash,
        "positions": [p.to_dict() for p in state.positions],
    }


def show() -> dict[str, Any]:
    """Current state, holdings, P&L, drawdown, benchmark."""
    if not pf.portfolio_exists():
        return {"status": "not_initialised"}
    state = pf.load_state()
    symbols = [p.symbol for p in state.positions]
    prices = pf.fetch_latest_prices(symbols) if symbols else {}
    holdings, rows = pf.compute_holdings_value(state, prices)
    total_value = round(state.cash + holdings, 2)
    total_return_pct = (
        0.0 if state.initial_capital <= 0
        else (total_value - state.initial_capital) / state.initial_capital * 100.0
    )
    bench = pf.load_benchmark()
    bench_snaps = bench.get("snapshots", [])
    benchmark_value = bench_snaps[-1]["value"] if bench_snaps else state.initial_capital
    bench_return_pct = (
        0.0 if state.initial_capital <= 0
        else (benchmark_value - state.initial_capital) / state.initial_capital * 100.0
    )
    return {
        "status": "ok",
        "initial_capital": state.initial_capital,
        "cash": state.cash,
        "holdings_value": holdings,
        "total_value": total_value,
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(state.max_drawdown_pct, 2),
        "benchmark_value": benchmark_value,
        "benchmark_return_pct": round(bench_return_pct, 2),
        "positions": rows,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def buy(
    symbol: str,
    shares: int,
    price: Optional[float] = None,
    reason: str = "",
    signal_ref: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Record a paper BUY. Returns the transaction + updated state summary."""
    if not pf.portfolio_exists():
        return {"status": "not_initialised"}
    if price is None:
        from . import market
        info = market.latest_price(symbol)
        if info.get("price") is None:
            return {"status": "error", "error": f"no price for {symbol}"}
        price = float(info["price"])
    state, txn = pf.buy(
        symbol=symbol, shares=shares, price=price,
        reason=reason, signal_ref=signal_ref,
    )
    return {
        "status": "filled",
        "side": "BUY",
        "transaction": txn.to_dict(),
        "cash_after": state.cash,
        "position": next(
            (p.to_dict() for p in state.positions if p.symbol == symbol), None
        ),
    }


def sell(
    symbol: str,
    shares: Optional[int] = None,
    price: Optional[float] = None,
    reason: str = "",
    signal_ref: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Record a paper SELL. Returns the transaction + updated state summary."""
    if not pf.portfolio_exists():
        return {"status": "not_initialised"}
    if price is None:
        from . import market
        info = market.latest_price(symbol)
        if info.get("price") is None:
            return {"status": "error", "error": f"no price for {symbol}"}
        price = float(info["price"])
    state, txn = pf.sell(
        symbol=symbol, shares=shares, price=price,
        reason=reason, signal_ref=signal_ref,
    )
    return {
        "status": "filled",
        "side": "SELL",
        "transaction": txn.to_dict(),
        "cash_after": state.cash,
        "remaining_position": next(
            (p.to_dict() for p in state.positions if p.symbol == symbol), None
        ),
    }


def snapshot() -> dict[str, Any]:
    """Take a mark-to-market snapshot and return the resulting series."""
    if not pf.portfolio_exists():
        return {"status": "not_initialised"}
    state = pf.load_state()
    symbols = [p.symbol for p in state.positions]
    prices = pf.fetch_latest_prices(symbols) if symbols else {}
    snap = pf.take_snapshot(prices=prices)
    return {"status": "ok", "snapshot": snap.to_dict(),
            "max_drawdown_pct": state.max_drawdown_pct}


def history(days: int = 90) -> dict[str, Any]:
    """Return snapshot history within the window."""
    if not pf.portfolio_exists():
        return {"status": "not_initialised"}
    snaps = pf.load_snapshots()
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    def parse(ts: str) -> datetime:
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    filtered = [s for s in snaps if parse(s.timestamp) >= cutoff] or snaps
    return {
        "status": "ok",
        "count": len(filtered),
        "snapshots": [s.to_dict() for s in filtered],
    }


def decisions(
    last: Optional[int] = None, symbol: Optional[str] = None,
) -> dict[str, Any]:
    """Return the (filtered) trade ledger."""
    if not pf.portfolio_exists():
        return {"status": "not_initialised"}
    txns = pf.load_transactions()
    total = len(txns)
    if symbol:
        txns = [t for t in txns if t.symbol == symbol]
    if last is not None and last > 0:
        txns = txns[-last:]
    return {
        "status": "ok",
        "count": len(txns),
        "total": total,
        "transactions": [t.to_dict() for t in txns],
    }
