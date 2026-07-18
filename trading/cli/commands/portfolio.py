"""``trading portfolio …`` — paper portfolio management.

Subcommands (all support ``--json`` for machine output)::

    init       --capital 100000 [--force]      Create or reset the paper portfolio
    show                                       Current state, P&L, drawdown, benchmark
    buy        SYMBOL --shares N [--all]       Paper buy (pulls live price, uses signal reason)
    sell       SYMBOL [--shares N]              Paper sell (full position if --shares omitted)
    snapshot                                   Mark-to-market snapshot (cron-ready)
    history    [--days 90] [--csv]              Value chart + benchmark overlay
    decisions  [--last N] [--symbol X]         Decision log with reasoning
"""
from __future__ import annotations

import io
import os
import sys
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .. import output
from ... import config as _config
from ...portfolio import engine as pf
from ...services import market, signal as signal_svc

# ── Helpers ───────────────────────────────────────────────────────────────
def _signal_for_reason(symbol: str) -> tuple[str, dict[str, Any]]:
    """Build a human-readable reason + signal_ref for a BUY/SELL.

    Best-effort: uses the live signal service. On any failure returns
    a generic reason and an empty signal_ref so trading still works
    offline.
    """
    try:
        sig = signal_svc.signal_for_symbol(symbol)
        score = sig.get("score", 0.0)
        rec = sig.get("recommendation", "")
        ind = sig.get("indicators", {})
        rsi = ind.get("rsi")
        signal_type = ind.get("signal", "HOLD")
        reason = (
            f"Signal: {rec} on {symbol} "
            f"(score={score:.0f}, signal={signal_type}"
            + (f", RSI={rsi:.1f}" if rsi is not None else "")
            + ")."
        )
        ref = {
            "score": score,
            "recommendation": rec,
            "rsi": rsi,
            "signal": signal_type,
            "source": sig.get("source", "?"),
        }
        return reason, ref
    except Exception as exc:  # noqa: BLE001
        return f"Manual trade on {symbol} (signal unavailable: {exc})", {}


def _resolve_price(symbol: str, override: Optional[float]) -> float:
    """Get the trade price: explicit override → market price → raise."""
    if override is not None and override > 0:
        return float(override)
    info = market.latest_price(symbol)
    price = info.get("price")
    if price is None or price <= 0:
        raise pf.PortfolioError(
            f"No live price for {symbol}; pass --price to override."
        )
    return float(price)


def _err(message: str, as_json: bool) -> int:
    """Print an error to stderr in a consistent shape and return exit 2."""
    if as_json:
        print(output.json_dumps({"error": message}), file=sys.stderr)
    else:
        print(f"❌ {message}", file=sys.stderr)
    return 2


def _ok(payload: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(output.json_dumps(payload))
    return 0


# ── Subcommand: init ─────────────────────────────────────────────────────
def init_cmd(capital: float, force: bool, as_json: bool, quiet: bool) -> int:
    if capital <= 0:
        return _err("Capital must be > 0", as_json)
    try:
        state = pf.init_portfolio(capital=capital, force=force)
    except pf.PortfolioExistsError as exc:
        return _err(str(exc), as_json)
    except pf.PortfolioError as exc:
        return _err(str(exc), as_json)

    payload = {
        "status": "initialised" if not force else "reset",
        "capital": state.initial_capital,
        "cash": state.cash,
        "positions": [],
        "portfolio_dir": pf._default_portfolio_dir(),
    }
    if as_json or quiet:
        return _ok(payload, as_json)
    if force:
        print("🔄 Paper portfolio RESET.")
    else:
        print("📋 Paper portfolio created.")
    print(f"   Capital:  KES {state.initial_capital:,.2f}")
    print(f"   Cash:     KES {state.cash:,.2f}")
    print(f"   Position: 0 / empty (100% cash)")
    print(f"   Storage:  {pf._default_portfolio_dir()}")
    return 0


# ── Subcommand: show ──────────────────────────────────────────────────────
def show_cmd(as_json: bool, quiet: bool) -> int:
    if not pf.portfolio_exists():
        return _err(
            "No portfolio found. Run 'trading portfolio init --capital 100000' first.",
            as_json,
        )
    try:
        state = pf.load_state()
    except pf.PortfolioError as exc:
        return _err(str(exc), as_json)

    symbols = [p.symbol for p in state.positions]
    prices = pf.fetch_latest_prices(symbols) if symbols else {}
    holdings, rows = pf.compute_holdings_value(state, prices)
    total_value = round(state.cash + holdings, 2)
    total_return_pct = (
        0.0 if state.initial_capital <= 0
        else (total_value - state.initial_capital) / state.initial_capital * 100.0
    )

    # Most recent benchmark value (if snapshots exist)
    bench = pf.load_benchmark()
    bench_snaps = bench.get("snapshots", [])
    benchmark_value = bench_snaps[-1]["value"] if bench_snaps else state.initial_capital
    bench_return_pct = (
        0.0 if state.initial_capital <= 0
        else (benchmark_value - state.initial_capital) / state.initial_capital * 100.0
    )

    payload = {
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

    if as_json or quiet:
        return _ok(payload, as_json)

    console = Console()
    console.print()
    console.print(Panel(
        _format_show_body(payload),
        title="📋 PAPER PORTFOLIO — Default",
        border_style="bold",
    ))
    if rows:
        console.print()
        _format_positions_table(rows, console=console)
    return 0


def _format_show_body(p: dict[str, Any]) -> str:
    def fmt_pct(v: float) -> str:
        return f"{v:+.2f}%"
    def fmt_kes(v: float) -> str:
        return f"KES {v:,.2f}"
    return (
        f"  Initial Capital     {fmt_kes(p['initial_capital'])}\n"
        f"  Current Value       {fmt_kes(p['total_value'])}\n"
        f"  Total Return        {fmt_pct(p['total_return_pct'])}\n"
        f"  Max Drawdown        {p['max_drawdown_pct']:+.2f}%\n"
        f"  Cash                {fmt_kes(p['cash'])}\n"
        f"  Holdings Value      {fmt_kes(p['holdings_value'])}\n"
        f"  Benchmark           {fmt_kes(p['benchmark_value'])}  "
        f"({fmt_pct(p['benchmark_return_pct'])})"
    )


def _format_positions_table(rows: list[dict[str, Any]], console: Optional[Console] = None) -> None:
    t = Table(show_header=True, header_style="bold", title="POSITIONS")
    t.add_column("Symbol")
    t.add_column("Shares", justify="right")
    t.add_column("Avg Cost", justify="right")
    t.add_column("Last Price", justify="right")
    t.add_column("Value (KES)", justify="right")
    t.add_column("P&L (KES)", justify="right")
    t.add_column("P&L %", justify="right")
    for r in rows:
        t.add_row(
            r["symbol"],
            f"{r['shares']}",
            f"{r['avg_cost']:.2f}",
            f"{r['last_price']:.2f}",
            f"{r['value']:,.2f}",
            f"{r['pnl']:+,.2f}",
            f"{r['pnl_pct']:+.2f}%",
        )
    (console or Console()).print(t)


# ── Subcommand: buy ───────────────────────────────────────────────────────
def buy_cmd(
    symbol: str,
    shares: int,
    all_in: bool,
    price_override: Optional[float],
    as_json: bool,
    quiet: bool,
) -> int:
    if not pf.portfolio_exists():
        return _err("No portfolio — run 'trading portfolio init' first.", as_json)
    try:
        price = _resolve_price(symbol, price_override)
    except pf.PortfolioError as exc:
        return _err(str(exc), as_json)

    if all_in:
        # Compute max affordable shares
        state = pf.load_state()
        fee_per_share = max(round(price * pf.TRANSACTION_FEE_PCT, 2), pf.FEE_MIN)
        per_share = round(price + fee_per_share, 2)
        max_shares = int(state.cash // per_share)
        if max_shares < 1:
            return _err(
                f"Insufficient cash for even 1 share of {symbol} "
                f"(need {per_share:,.2f}, have {state.cash:,.2f})",
                as_json,
            )
        shares = max_shares

    if shares <= 0:
        return _err("Shares must be > 0 (or use --all)", as_json)

    reason, sig_ref = _signal_for_reason(symbol)
    try:
        state, txn = pf.buy(
            symbol=symbol, shares=shares, price=price,
            reason=reason, signal_ref=sig_ref,
        )
    except (pf.InsufficientCashError, pf.PortfolioError) as exc:
        return _err(str(exc), as_json)

    payload = {
        "status": "filled",
        "side": "BUY",
        "symbol": txn.symbol,
        "shares": txn.shares,
        "price": txn.price,
        "total": txn.total,
        "fee": txn.fee,
        "net_cash_delta": txn.net_cash_delta,
        "cash_after": state.cash,
        "position": next(
            (p.to_dict() for p in state.positions if p.symbol == symbol), None
        ),
        "reason": txn.reason,
        "signal_ref": txn.signal_ref,
    }
    if as_json or quiet:
        return _ok(payload, as_json)

    print(f"✅ BOUGHT {txn.shares} {txn.symbol} @ KES {txn.price:,.2f}")
    print(f"   Total:     KES {txn.total:,.2f}")
    print(f"   Fee:       KES {txn.fee:,.2f}")
    print(f"   Cash left: KES {state.cash:,.2f}")
    print(f"   Reason:    {txn.reason}")
    return 0


# ── Subcommand: sell ──────────────────────────────────────────────────────
def sell_cmd(
    symbol: str,
    shares: Optional[int],
    price_override: Optional[float],
    as_json: bool,
    quiet: bool,
) -> int:
    if not pf.portfolio_exists():
        return _err("No portfolio — run 'trading portfolio init' first.", as_json)
    try:
        price = _resolve_price(symbol, price_override)
    except pf.PortfolioError as exc:
        return _err(str(exc), as_json)

    reason, sig_ref = _signal_for_reason(symbol)
    try:
        state, txn = pf.sell(
            symbol=symbol, shares=shares, price=price,
            reason=reason, signal_ref=sig_ref,
        )
    except (pf.UnknownPositionError, pf.InsufficientSharesError, pf.PortfolioError) as exc:
        return _err(str(exc), as_json)

    payload = {
        "status": "filled",
        "side": "SELL",
        "symbol": txn.symbol,
        "shares": txn.shares,
        "price": txn.price,
        "total": txn.total,
        "fee": txn.fee,
        "net_cash_delta": txn.net_cash_delta,
        "realised_pnl": txn.realised_pnl,
        "cash_after": state.cash,
        "remaining_position": next(
            (p.to_dict() for p in state.positions if p.symbol == symbol), None
        ),
        "reason": txn.reason,
    }
    if as_json or quiet:
        return _ok(payload, as_json)

    pnl_str = f"+KES {txn.realised_pnl:,.2f}" if txn.realised_pnl and txn.realised_pnl >= 0 else f"KES {txn.realised_pnl:,.2f}"
    print(f"✅ SOLD {txn.shares} {txn.symbol} @ KES {txn.price:,.2f}")
    print(f"   Proceeds:  KES {txn.total:,.2f}")
    print(f"   Fee:       KES {txn.fee:,.2f}")
    print(f"   Realised:  {pnl_str}")
    print(f"   Cash now:  KES {state.cash:,.2f}")
    print(f"   Reason:    {txn.reason}")
    return 0


# ── Subcommand: snapshot ──────────────────────────────────────────────────
def snapshot_cmd(as_json: bool, quiet: bool) -> int:
    if not pf.portfolio_exists():
        return _err("No portfolio — run 'trading portfolio init' first.", as_json)
    state = pf.load_state()
    symbols = [p.symbol for p in state.positions]
    prices = pf.fetch_latest_prices(symbols) if symbols else {}
    snap = pf.take_snapshot(prices=prices)
    payload = {
        "status": "ok",
        "timestamp": snap.timestamp,
        "cash": snap.cash,
        "holdings_value": snap.holdings_value,
        "total_value": snap.total_value,
        "daily_return_pct": snap.daily_return_pct,
        "total_return_pct": snap.total_return_pct,
        "drawdown_pct": snap.drawdown_pct,
        "benchmark_value": snap.benchmark_value,
        "max_drawdown_pct": state.max_drawdown_pct,
    }
    if as_json or quiet:
        return _ok(payload, as_json)
    print(f"📸 Snapshot at {snap.timestamp}")
    print(f"   Total value:  KES {snap.total_value:,.2f}  ({snap.total_return_pct:+.2f}%)")
    print(f"   Holdings:     KES {snap.holdings_value:,.2f}")
    print(f"   Cash:         KES {snap.cash:,.2f}")
    print(f"   Daily return: {snap.daily_return_pct:+.2f}%")
    print(f"   Drawdown:     {snap.drawdown_pct:+.2f}%   (max {state.max_drawdown_pct:+.2f}%)")
    print(f"   Benchmark:    KES {snap.benchmark_value:,.2f}")
    return 0


# ── Subcommand: history ───────────────────────────────────────────────────
def history_cmd(days: int, as_csv: bool, as_json: bool, quiet: bool) -> int:
    if not pf.portfolio_exists():
        return _err("No portfolio — run 'trading portfolio init' first.", as_json)
    snaps = pf.load_snapshots()
    if not snaps:
        return _err("No snapshots yet — run 'trading portfolio snapshot'.", as_json)

    # Filter to last N days (by timestamp)
    cutoff = _now() - _td(days=days)
    filtered = [s for s in snaps if _parse_iso(s.timestamp) >= cutoff]
    if not filtered:
        filtered = snaps  # fall back to all if filter would yield empty

    if as_csv:
        sys.stdout.write(pf.snapshots_to_csv(filtered))
        return 0

    if as_json:
        return _ok(
            {"snapshots": [s.to_dict() for s in filtered], "count": len(filtered)},
            as_json,
        )

    # Render ASCII chart
    print(f"📈 Portfolio value — last {days} day(s) ({len(filtered)} snapshots)\n")
    _render_ascii_chart(filtered)

    # Key metrics
    initial = filtered[0].total_value
    final = filtered[-1].total_value
    total_ret = 0.0 if initial <= 0 else (final - initial) / initial * 100.0
    max_dd = max(s.drawdown_pct for s in filtered)
    bench_first = filtered[0].benchmark_value
    bench_last = filtered[-1].benchmark_value
    bench_ret = 0.0 if bench_first <= 0 else (bench_last - bench_first) / bench_first * 100.0

    t = Table(title="METRICS", show_header=True, header_style="bold")
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")
    t.add_row("Period return", f"{total_ret:+.2f}%")
    t.add_row("Benchmark return", f"{bench_ret:+.2f}%")
    t.add_row("Max drawdown", f"{max_dd:+.2f}%")
    t.add_row("Start value", f"KES {initial:,.2f}")
    t.add_row("End value", f"KES {final:,.2f}")
    t.add_row("Start benchmark", f"KES {bench_first:,.2f}")
    t.add_row("End benchmark", f"KES {bench_last:,.2f}")
    Console().print(t)
    return 0


def _now() -> "datetime":  # type: ignore[name-defined]
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _td(days: int):
    from datetime import timedelta
    return timedelta(days=days)


def _parse_iso(ts: str):
    from datetime import datetime
    # Python's fromisoformat handles +03:00 but not always Z — be defensive
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _render_ascii_chart(snaps: list) -> None:
    """Render a 60-row, 80-col ASCII value chart with benchmark overlay."""
    if len(snaps) < 2:
        print("(need at least 2 snapshots to chart)")
        return
    width = 60
    height = 18
    total_vals = [s.total_value for s in snaps]
    bench_vals = [s.benchmark_value for s in snaps]
    all_vals = total_vals + bench_vals
    vmin = min(all_vals)
    vmax = max(all_vals)
    span = vmax - vmin if vmax > vmin else 1.0

    def scale(vals: list[float]) -> list[int]:
        return [int(round((v - vmin) / span * (height - 1))) for v in vals]

    total_scaled = scale(total_vals)
    bench_scaled = scale(bench_vals)

    # Downsample to ``width`` columns
    n = len(snaps)
    if n > width:
        idx = [int(round(i * (n - 1) / (width - 1))) for i in range(width)]
    else:
        idx = list(range(n))

    grid: list[list[str]] = [[" "] * width for _ in range(height)]
    # Draw benchmark as '.' and portfolio as '#'; collisions prefer '#'
    for col, i in enumerate(idx):
        b_row = bench_scaled[i]
        t_row = total_scaled[i]
        # y=0 is top → invert
        grid[height - 1 - b_row][col] = "."
        grid[height - 1 - t_row][col] = "#"
    for row in grid:
        print("  " + "".join(row))
    # x-axis time labels
    first_ts = snaps[idx[0]].timestamp[:10]
    last_ts = snaps[idx[-1]].timestamp[:10]
    print(f"  {first_ts}" + " " * (width - 20) + f"{last_ts}")
    print(f"  Legend:  # = Portfolio,  . = Benchmark   (range {vmin:,.0f} – {vmax:,.0f} KES)")


# ── Subcommand: decisions ─────────────────────────────────────────────────
def decisions_cmd(
    last: Optional[int],
    symbol: Optional[str],
    as_json: bool,
    quiet: bool,
) -> int:
    if not pf.portfolio_exists():
        return _err("No portfolio — run 'trading portfolio init' first.", as_json)
    txns = pf.load_transactions()
    total_log = len(txns)

    if symbol:
        txns = [t for t in txns if t.symbol == symbol]
    if last is not None and last > 0:
        txns = txns[-last:]

    if as_json or quiet:
        return _ok(
            {"transactions": [t.to_dict() for t in txns], "count": len(txns), "total": total_log},
            as_json,
        )

    if not txns:
        print(f"\n📜 DECISION LOG (0 of {total_log})")
        print("─" * 80)
        print("  No trades yet.")
        return 0

    print(f"\n📜 DECISION LOG (showing {len(txns)} of {total_log})")
    print("─" * 80)
    for t in txns:
        ts = t.timestamp[:10]
        pnl = ""
        if t.realised_pnl is not None:
            sign = "+" if t.realised_pnl >= 0 else ""
            pnl = f"  {sign}KES {t.realised_pnl:,.2f} realised"
        reason = t.reason or "Manual trade"
        print(
            f"  {ts}  {t.action:<4s} {t.symbol:<6s} "
            f"{t.shares:>4d} @ {t.price:>7.2f}  KES {t.total:>9,.2f}{pnl}"
        )
        print(f"           {reason}")
    return 0
