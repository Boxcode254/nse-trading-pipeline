#!/usr/bin/env python3
"""
Auto-trader — executes the day's target-allocation plan in the paper portfolio.

Usage::
    cd ~/.trading && python3 -m trading.auto_trader
    cd ~/.trading && python3 -m trading.auto_trader --dry-run

Schedule (live cron): 10:30 EAT weekdays (job auto-trader-execution).

DATA TIMING (explicit):
  Strategy + signals use TradingView *daily* bars (prior session EOD).
  Execution prices prefer mystocks 15-min delayed cache, then Mansa, then CSV.
  This is NOT true intraday signal trading — it is EOD-bar rebalancing run
  mid-session so gap filter can skip stale open moves. Market-close MTM is
  a separate 15:30 job.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the project root is on the path
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from trading import config
from trading.execution import ExecutionEngine, OrderRequest
from trading.execution.models import AccountInfo
from trading.execution.brokers import PaperBroker
from trading.execution.safety import SafetyEngine
from trading.execution.run_lock import RunLock
from trading.execution.order_store import OrderStore
from trading.execution.retry import call_with_timeout
from trading.portfolio import engine as port_engine
from trading import replay as replay_module

# === POLICY TRIPWIRE (do not remove) ===
# News/headlines (news_store.json, Business Daily feeds, market_intel cache) are
# CONTEXT/ALERTING ONLY. They must NEVER become an execution input. The auto-trader
# reads ONLY portfolio state + prices (MTM) + allocation rules. See ../POLICY.md.
# Wiring sentiment/news scores into rebalance weights is explicitly FORBIDDEN.


def _make_safety() -> SafetyEngine:
    """Create a SafetyEngine from the EXECUTION_CONFIG."""
    cfg = config.EXECUTION_CONFIG
    safety = SafetyEngine()
    # Apply config to safety engine (if needed)
    # For now, we assume the SafetyEngine reads from config.EXECUTION_CONFIG via import
    # but we can also set attributes directly if needed.
    return safety


def _load_state(portfolio_dir: Path) -> dict[str, Any]:
    """Load the current portfolio state."""
    state_path = portfolio_dir / "state.json"
    if not state_path.exists():
        return {"cash": 0.0, "positions": [], "initial_capital": 0.0}
    with open(state_path) as f:
        return json.load(f)


def _mystocks_price(symbol: str) -> float | None:
    """Try to get today's 15-min delayed price from mystocks cache.
    
    Staleness guard: if cache file is > 10 min old, treat as missing
    and fall through to Mansa live fetch.
    """
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cache_path = Path(config.DECISION_CACHE_DIR) / f"live-prices-{today}.json"
        if not cache_path.exists():
            return None
        # Staleness check: cache mtime > 10 min = stale
        mtime = os.path.getmtime(cache_path)
        if time.time() - mtime > 600:  # 10 minutes
            logging.warning(f"mystocks cache for {symbol} stale (age {int(time.time()-mtime)}s), falling back to Mansa")
            return None
        with open(cache_path) as f:
            data = json.load(f)
        price = data.get("stocks", {}).get(symbol)
        if price and 1 < price < 1000:
            return float(price)
    except Exception:
        pass
    return None


def _mansa_price(symbol: str) -> float | None:
    """Try to get current price from Mansa API (primary source).
    Uses shared cache (5 min TTL during market hours)."""
    # First check cache
    try:
        import sys
        sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
        from mansa_quote_cache import get as mansa_get
        cached = mansa_get(symbol)
        if cached is not None:
            return cached
    except Exception:
        pass
    
    # Fall back to live API call
    import urllib.request
    key = os.environ.get("MANSA_API_KEY", "")
    if not key:
        return None
    try:
        url = f"https://mansaapi.com/api/v1/markets/exchanges/KENYA/stocks/{symbol}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data.get("success"):
            price = data["data"].get("price")
            if price and 1 < float(price) < 1000:
                price_float = float(price)
                # Cache for other callers
                try:
                    import sys
                    sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
                    from mansa_quote_cache import set_one as mansa_set
                    mansa_set(symbol, price_float)
                except Exception:
                    pass
                return price_float
    except Exception:
        pass
    return None


def _get_price_from_services(symbol: str) -> float | None:
    """Try mystocks first (15-min delayed), then Mansa, then CSV close."""
    # 1. Try mystocks (15-min delayed page scrape)
    p = _mystocks_price(symbol)
    if p is not None:
        return p
    # 2. Try Mansa API (secondary — free tier may be stale)
    p = _mansa_price(symbol)
    if p is not None:
        return p
    # 3. Fall back to NSE CSV close
    try:
        data_dir = Path(config.HOME) / "data"
        csv_path = data_dir / f"nse_{symbol}.csv"
        if csv_path.exists():
            import csv

            with open(csv_path) as f:
                rows = list(csv.DictReader(f))
            if rows:
                _last_date = rows[-1].get("date", "").strip()
                _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if _last_date == _today and len(rows) >= 2:
                    last = rows[-2]
                elif _last_date == _today:
                    return None  # No prior-day data — can't price reliably
                else:
                    last = rows[-1]
                return float(last.get("close", last.get("Close", 0)))
    except Exception:
        pass
    return None


def _price_map(symbols: list[str]) -> dict[str, float]:
    """Build a {symbol: price} dict for all *symbols* from cached data."""
    prices: dict[str, float] = {}
    for sym in symbols:
        price = _get_price_from_services(sym)
        if price and price > 0:
            prices[sym] = price
    return prices


def _sector_of(symbol: str) -> str:
    return config.get_sector(symbol)


def _plan_delta(trade: dict) -> int:
    """Extract incremental share qty from a plan trade (shares contract).

    Prefer ``delta_shares``; fall back to ``shares`` which is defined as an
    alias of delta (never an absolute target holding).
    """
    if trade.get("delta_shares") is not None:
        return max(0, int(trade["delta_shares"]))
    return max(0, int(trade.get("shares") or 0))


def _pct(value: float, total: float) -> float:
    return (value / total * 100) if total else 0.0


# ── Report builder ──────────────────────────────────────────────────────────
class AutoTraderReport:
    """Collects auto-trader actions and formats them as plain English."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.stocks_bought: list[dict] = []
        self.stocks_sold: list[dict] = []
        self.stocks_skipped: list[dict] = []
        self.portfolio_before: dict = {}
        self.portfolio_after: dict = {}
        self.timestamp = datetime.now(timezone.utc)

    @property
    def _prefix(self) -> str:
        return "[REPLAY] " if replay_module.is_replay() else ""

    def add_buy(self, symbol: str, shares: int, price: float, value: float, reason: str) -> None:
        self.stocks_bought.append(
            dict(symbol=symbol, shares=shares, price=price, value=value, reason=reason)
        )

    def add_sell(self, symbol: str, shares: int, price: float, value: float, reason: str) -> None:
        self.stocks_sold.append(
            dict(symbol=symbol, shares=shares, price=price, value=value, reason=reason)
        )

    def add_skip(self, symbol: str, reason: str) -> None:
        self.stocks_skipped.append(dict(symbol=symbol, reason=reason))

    def set_portfolio(self, before: dict, after: dict) -> None:
        self.portfolio_before = before
        self.portfolio_after = after

    def build(self) -> str:
        """Build the plain-English report."""
        now_str = self.timestamp.strftime("%A, %d %B %Y at %H:%M")
        buf: list[str] = []
        buf.append(f"{self._prefix}📊 **End-of-Day Trading Report** — {now_str}")
        buf.append("")

        # ── Portfolio summary ──
        before = self.portfolio_before
        after = self.portfolio_after
        buf.append("━━━ 💰 Portfolio ━━━")
        cash_b = before.get("cash", 0)
        cash_a = after.get("cash", 0)
        total_b = before.get("total_value", cash_b)
        total_a = after.get("total_value", cash_a)
        pnl = total_a - before.get("initial_capital", total_a)
        initial = before.get("initial_capital", 0)
        pnl_pct = ((total_a - initial) / initial * 100) if initial else 0

        buf.append(f"  Portfolio value:    KES {total_b:>10,.0f} → KES {total_a:>10,.0f}")
        buf.append(f"  Cash:               KES {cash_b:>10,.0f} → KES {cash_a:>10,.0f}")
        buf.append(f"  Return (total):     KES {pnl:>+10,.0f} ({pnl_pct:+.1f}%)")
        buf.append("")

        # ── Trades executed ──
        if not self.stocks_bought and not self.stocks_sold:
            buf.append("━━━ 📭 No Trades Today ━━━")
            buf.append("  The system found no high-conviction opportunities.")
            buf.append("")
        else:
            if self.stocks_bought:
                buf.append("━━━ 🟢 Bought ━━━")
                for t in self.stocks_bought:
                    buf.append(
                        f"  **{t['symbol']}**: {t['shares']} shares @ KES {t['price']:,.2f} "
                        f"= KES {t['value']:,.0f}"
                    )
                    buf.append(f"    → {t['reason']}")
                buf.append("")
            if self.stocks_sold:
                buf.append("━━━ 🔴 Sold ━━━")
                for t in self.stocks_sold:
                    buf.append(
                        f"  **{t['symbol']}**: {t['shares']} shares @ KES {t['price']:,.2f} "
                        f"= KES {t['value']:,.0f}"
                    )
                    buf.append(f"    → {t['reason']}")
                buf.append("")

        # ── Why some were skipped ──
        if self.stocks_skipped:
            buf.append("━━━ ⏭️ Considered but Skipped ━━━")
            for s in self.stocks_skipped:
                symbol = s["symbol"]
                reason = s["reason"]
                # Truncate long reasoning
                if len(reason) > 120:
                    reason = reason[:117] + "..."
                buf.append(f"  **{symbol}**: {reason}")
            buf.append("")

        # ── Positions held (after trading) ──
        positions = after.get("positions", [])
        if positions:
            buf.append(f"━━━ 📋 Positions Held ({len(positions)}) ━━━")
            for p in positions:
                sym = p["symbol"]
                shares = p["shares"]
                cost = p["avg_cost"]
                total_vals = p.get("current_value", 0) or (shares * cost)
                sector = _sector_of(sym)
                buf.append(
                    f"  {sym:<5s}  {shares:>4d} shares @ KES {cost:<8.2f}  "
                    f"valued at KES {total_vals:>8,.0f}  ({sector})"
                )
            buf.append("")

        # ── Sector exposure warning ──
        sector_values: dict[str, float] = {}
        for p in positions:
            sec = _sector_of(p["symbol"])
            val = p.get("current_value", 0) or (p["shares"] * p["avg_cost"])
            sector_values[sec] = sector_values.get(sec, 0) + val

        over_limit = []
        for sec, val in sector_values.items():
            pct = _pct(val, total_a)
            cap = config.sector_cap(sec)
            # Warn only when over HARD (momentum-adjusted), so a trending winner
            # doesn't spam the sector warning at the old 25% line.
            if pct > cap["hard"]:
                over_limit.append((sec, pct, cap["hard"]))

        if over_limit:
            buf.append("━━━ ⚠️ Sector Warning ━━━")
            for sec, pct, hard in over_limit:
                buf.append(
                    f"  {sec} at {pct:.0f}% exceeds the {hard:.0f}% limit"
                )
            buf.append("  Consider diversifying into other sectors on next trade day.")
            buf.append("")

        return "\n".join(buf)


# ── Phase 0 reconciliation — verify an executed trade's fill against intent. ──
_RECON_PRICE_TOL_PCT = 0.5


# ── Pending-reconcile ledger ───────────────────────────────────────────────
# When a live order returns UNKNOWN (timeout with unreconciled outcome), the
# paper broker's place_order worker may have completed in the background
# (call_with_timeout leaves the worker running). We must NOT just drop it as a
# skip — we track it and force a position re-read on the NEXT run to determine
# the true state. This closes the ABSA-style "sell failed: UNKNOWN, dropped"
# gap where the book silently kept the un-reduced position.
_PENDING_RECONCILE_PATH = Path.home() / ".trading" / "portfolio" / "pending_reconcile.json"


def _record_pending_reconcile(symbol: str, side: str, intended_shares: int, price: float) -> None:
    """Record an unresolved (UNKNOWN) order so the next run force-reconciles it."""
    try:
        if _PENDING_RECONCILE_PATH.exists():
            try:
                data = json.loads(_PENDING_RECONCILE_PATH.read_text() or "{}")
            except (json.JSONDecodeError, ValueError):
                data = {}  # tolerate empty/corrupt marker file
        else:
            data = {}
        data[symbol] = {
            "side": side,
            "intended_shares": intended_shares,
            "price": price,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        _PENDING_RECONCILE_PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass  # non-fatal: tracking failure must never break the run


def _resolve_pending_reconcile(portfolio_dir: Path, report: "AutoTraderReport") -> None:
    """At run start, re-read actual positions for any UNKNOWN orders from a prior
    run and report whether they actually executed. Clears the marker.

    For a PAPER broker an UNKNOWN almost always means the synchronous
    place_order worker finished after the timeout — so the position usually DID
    change. We compare the live holding vs the intended delta and report the
    truth instead of leaving the book ambiguous.
    """
    if not _PENDING_RECONCILE_PATH.exists():
        return
    try:
        pending = json.loads(_PENDING_RECONCILE_PATH.read_text())
    except Exception:
        return
    if not pending:
        return
    state = _load_state(portfolio_dir)
    live = {p["symbol"]: p["shares"] for p in state.get("positions", [])}
    for sym, info in list(pending.items()):
        intended = info.get("intended_shares", 0)
        side = info.get("side", "")
        actual = live.get(sym, 0)
        # We cannot know the pre-order baseline from here; report the live state
        # and that reconciliation was forced. The human/next-run sees a resolved
        # position rather than a dangling UNKNOWN.
        report.add_skip(
            sym,
            f"PENDING-RECONCILE resolved: {side} {intended} {sym} — live holding now "
            f"{actual} sh. UNKNOWN order force-reconciled against portfolio state.",
        )
        pending.pop(sym, None)
    try:
        if pending:
            _PENDING_RECONCILE_PATH.write_text(json.dumps(pending, indent=2))
        else:
            _PENDING_RECONCILE_PATH.unlink()
    except Exception:
        pass


def _reconcile_trade(
    symbol: str, side: str, intended_shares: int, intended_price: float, txn, *, alerts_path: str | None = None
) -> list[str]:
    """Compare the executed transaction to what was intended.

    Returns a list of mismatch descriptions (empty == clean). Alerts on any
    mismatch with CRITICAL severity (money/order-state risk).
    """
    from trading.execution.alerting import alert

    mismatches: list[str] = []
    filled_shares = getattr(txn, "shares", 0)
    filled_price = getattr(txn, "price", 0.0)

    if filled_shares < intended_shares:
        mismatches.append(
            f"PARTIAL FILL {symbol} {side}: intended {intended_shares}, "
            f"got {filled_shares} ({intended_shares - filled_shares} short)"
        )
    if intended_price and intended_price > 0:
        slip = abs(filled_price - intended_price) / intended_price * 100
        if slip > _RECON_PRICE_TOL_PCT:
            mismatches.append(
                f"PRICE SLIP {symbol} {side}: intended {intended_price:.4f}, "
                f"filled {filled_price:.4f} ({slip:+.2f}%)"
            )

    if mismatches:
        alert(
            "Auto-trader reconciliation mismatch: " + "; ".join(mismatches),
            severity="CRITICAL",
            context={
                "symbol": symbol,
                "side": side,
                "intended_shares": intended_shares,
                "intended_price": intended_price,
            },
            alerts_path=alerts_path,
        )
    return mismatches


def _record_fill(
    safety: SafetyEngine,
    *,
    side: str,
    symbol: str,
    shares: int,
    price: float,
    fee: float,
    realised_pnl: float | None = None,
) -> None:
    """Record a fill with the safety engine for daily counters."""
    from trading.execution.models import OrderResult

    safety.record_trade(
        OrderResult(
            success=True,
            order_id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            quantity=shares,
            price=price,
            total=round(shares * price, 2),
            fee=fee,
            status="filled",
            message="paper fill",
            timestamp=datetime.now(timezone.utc).isoformat(),
            realised_pnl=realised_pnl,
        )
    )


def _maybe_snapshot(prices: dict[str, float], portfolio_dir: Path) -> None:
    """Append MTM snapshot after live trades (no-op on empty prices)."""
    try:
        if not prices:
            return
        port_engine.take_snapshot(prices=prices, dir_path=str(portfolio_dir))
    except Exception:
        pass  # snapshot must never block trading


def _port_state_for_safety(positions: list[dict]) -> dict[str, Any]:
    """Convert position dicts to the shape expected by SafetyEngine."""
    norm_positions: dict[str, Any] = {}
    for p in positions:
        shares = p.get("shares", 0)
        avg_cost = p.get("avg_cost", 0.0)
        if avg_cost and shares:
            avg = avg_cost / shares
        else:
            avg = 0.0
        norm_positions[p["symbol"]] = {
            "shares": shares,
            "avg_cost": avg,
            "value": p.get("current_value", 0.0) or (shares * p["avg_cost"]),
        }
    return {"positions": norm_positions}


def _account_info(cash: float, total_value: float, available_cash: float, positions_count: int) -> "AccountInfo":
    """Build an AccountInfo snapshot for the safety gate.

    MUST return an AccountInfo (dataclass), not a plain dict — safety.check_order
    reads attributes (account.equity, account.cash, ...) which a dict lacks.
    """
    return AccountInfo(
        cash=cash,
        equity=total_value,
        buying_power=available_cash,
        positions_count=positions_count,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        currency="KES",
        broker="paper",
    )


def _current_drawdown_pct(portfolio_dir: Path | None = None) -> float:
    """Return current drawdown % from snapshots.json (engineering convenience)."""
    if portfolio_dir is None:
        portfolio_dir = Path(os.path.expanduser("~/.trading/portfolio"))
    try:
        snaps = port_engine.load_snapshots(portfolio_dir)
        if snaps:
            dds = port_engine.compute_drawdown(snaps)
            return max(dds) if dds else 0.0
    except Exception:
        pass
    return 0.0


def run_auto_trade(dry_run: bool = False) -> AutoTraderReport:
    """Execute the day's allocation recommendations in the paper portfolio.

    Parameters
    ----------
    dry_run : bool
        If True, only simulate without executing trades.

    Returns
    -------
    AutoTraderReport with all actions taken.
    """
    from trading.target_allocation import generate_rebalance_plan

    report = AutoTraderReport()

    # ── Non-trading-day guard (defense-in-depth) ─────────────────────
    # The live cron fires Mon–Fri only, but an ad-hoc/manual invocation or a
    # mis-set schedule must never mutate the paper ledger on a closed market.
    # NSE trades Mon–Fri; skip Sat (5) / Sun (6). Mirrors gap_scan.py's
    # weekday() convention so the whole pipeline treats weekends identically.
    _now = datetime.now(timezone.utc)
    if _now.weekday() >= 5:
        report.add_skip(
            "MARKET CLOSED",
            f"NSE is closed on {_now.strftime('%A %d %b %Y')} — the auto-trader "
            f"does not execute on non-trading days. No orders were placed.",
        )
        return report

    # Safety layer — one instance for the whole run (EXECUTION_CONFIG)
    safety = _make_safety()
    safety.reset_daily()  # once per day, not per order

    # Phase 1 — sync the risk gate with the live MTM equity curve + macro.
    # 1) Push the latest portfolio drawdown into the safety engine so the
    #    drawdown halt reflects the real equity curve (snapshots.json), not
    #    just in-memory state.
    try:
        dd_status = safety.update_drawdown(_current_drawdown_pct(portfolio_dir=None))
        if dd_status.get("halted"):
            report.add_skip(
                "RISK GATE",
                f"Drawdown halt active: {dd_status['drawdown_pct']:.2f}% "
                f">= {dd_status['limit']:.2f}% — no new trades",
            )
    except Exception as exc:
        report.add_skip("RISK GATE", f"drawdown sync failed (non-fatal): {exc}")

    # 2) Refresh the macro breaker from live NSE watchlist prices (breadth +
    #    composite index change + dispersion). Fail-open: if the fetch fails,
    #    the breaker stays in its current state and never trips on a miss.
    try:
        macro_res = safety.refresh_macro()
        if macro_res.get("breaker", {}).get("tripped"):
            report.add_skip(
                "RISK GATE",
                f"Macro circuit breaker tripped: "
                f"{macro_res['breaker'].get('reason', 'market stress')}",
            )
    except Exception as exc:
        report.add_skip("RISK GATE", f"macro refresh failed (non-fatal): {exc}")

    # 1. Load portfolio state
    if replay_module.is_replay():
        replay_module.ensure_replay_env()
        portfolio_dir = replay_module.sandbox_portfolio_dir()
        replay_module.bootstrap_sandbox(portfolio_dir)
        report.add_skip("REPLAY", f"Sandbox portfolio dir: {portfolio_dir}")
    else:
        portfolio_dir = Path(os.path.expanduser("~/.trading/portfolio"))
    state_path = portfolio_dir / "state.json"
    state = _load_state(portfolio_dir)
    # Force-reconcile any UNKNOWN orders left dangling from a prior run BEFORE
    # building today's trade list, so ambiguous positions don't persist.
    _resolve_pending_reconcile(portfolio_dir, report)
    initial_capital = state.get("initial_capital", 100_000.0)
    cash = state.get("cash", 0.0)
    current_positions = state.get("positions", [])

    # 1b. Engine agreement gate (defense-in-depth).
    try:
        from trading.target_allocation import verify_target_agreement

        # Bounded: verify_target_agreement -> generate_proposal fetches regime
        # + ranking price history (TradingView) with no inherent timeout. Cap
        # the whole gate so a slow upstream can never stall the run; on timeout
        # we fail-open (hold fire pending reconciliation) rather than block.
        completed, agree, err = call_with_timeout(
            lambda: verify_target_agreement(nse_only=True), 20.0
        )
        if not completed:
            report.add_skip(
                "ENGINE-AGREEMENT",
                "verification timed out (slow market data) — holding fire "
                "until reconciled",
            )
        elif agree is not None and not agree.get("agreed", True):
            report.add_skip(
                "ENGINE-AGREEMENT",
                f"target_allocation vs decision diverge "
                f"(max {agree.get('max_abs_diff', 0):.1f}% > "
                f"tol {agree.get('tolerance', 0):.1f}%) — holding fire until "
                f"reconciled",
            )
            return report
    except Exception as exc:  # noqa: BLE001
        report.add_skip(
            "ENGINE-AGREEMENT", f"verification skipped (non-fatal): {exc}"
        )

    # Drop suspended/halted names from the auto-trade universe. They remain in
    # state.json as a manual hold; the engine simply refuses to BUY/SELL them.
    for p in list(current_positions):
        if p["symbol"] in config.SUSPENDED_SYMBOLS:
            current_positions.remove(p)
            report.add_skip(
                p["symbol"],
                "SUSPENDED on NSE — excluded from auto-trading (manual hold only)",
            )

    # Build current holding dict {symbol: shares}
    holdings: dict[str, int] = {}
    position_cost: dict[str, float] = {}
    for p in current_positions:
        sym = p["symbol"]
        holdings[sym] = p["shares"]
        position_cost[sym] = p["avg_cost"]

    # Current portfolio total
    invested = sum(
        p.get("current_value", 0) or (p["shares"] * p["avg_cost"])
        for p in current_positions
    )
    total_before = cash + invested

    report.set_portfolio(
        {
            "cash": cash,
            "total_value": total_before,
            "initial_capital": initial_capital,
            "positions": current_positions,
        },
        {},
    )

    # 2. Get target-based rebalance plan (strategic sector allocation + signal gating)
    #    SHARES CONTRACT: plan emits delta_shares (shares to trade NOW).
    #    Never interpret plan qty as an absolute target holding.
    buy_list: list[dict] = []
    sell_list: list[dict] = []
    try:
        plan = generate_rebalance_plan(dry_run=True)
        # Check for plan-level constraint violations before processing trades
        for v in plan.get("violations", []):
            sv = v.get("severity", "WARN")
            sym = v.get("symbol", "PORTFOLIO")
            report.add_skip(sym, f"[{sv}] {v.get('type', 'CONSTRAINT')}: {v.get('message', '')}")
        for t in plan.get("trades", []):
            delta = _plan_delta(t)
            if delta <= 0:
                continue
            if t["side"] == "BUY":
                buy_list.append(
                    dict(
                        symbol=t["symbol"],
                        delta_shares=delta,
                        conviction="moderate",
                        reason=t["reason"],
                    )
                )
            elif t["side"] == "SELL":
                sell_list.append(
                    dict(
                        symbol=t["symbol"],
                        delta_shares=delta,
                        reason=t["reason"],
                        stop_loss=False,
                        sector_cap=False,
                    )
                )
        if not buy_list and not sell_list:
            report.add_skip("ALL", "Portfolio is balanced — no trades from target allocation")
    except Exception as exc:
        report.add_skip("ALL", f"Target allocation engine failed: {exc}")

    # 3. Get current prices for all symbols we care about
    all_symbols = sorted(
        set(
            list(holdings.keys())
            + [b["symbol"] for b in buy_list]
            + [s["symbol"] for s in sell_list]
        )
    )
    prices = _price_map(all_symbols)

    # 4. Stop-loss check: auto-sell positions that hit the loss threshold.
    #    Uses the SAME helper the safety gate uses (safety.should_stop_loss),
    #    so the auto-trader and manual CLI never disagree on what "stopped" means.
    #    Do NOT report sells here — report only after execution (step 7).
    sl_portfolio_state = {
        "positions": {
            p["symbol"]: {
                "shares": p["shares"],
                "avg_cost": p["avg_cost"],
                # value uses the live price if available, else current_value
                "value": (prices.get(p["symbol"]) or 0.0) * p["shares"]
                or p.get("current_value", 0),
            }
            for p in current_positions
        }
    }
    for p in current_positions:
        sym = p["symbol"]
        sl = safety.should_stop_loss(sym, sl_portfolio_state)
        if sl is not None and sl["stopped"]:
            already_in_sell = any(s["symbol"] == sym for s in sell_list)
            if not already_in_sell:
                sell_list.append(
                    dict(
                        symbol=sym,
                        delta_shares=int(p["shares"]),
                        reason=(
                            f"Stop-loss triggered: {sym} is {sl['loss_pct']:.1f}% "
                            f"below avg cost of KES {sl['avg_cost']:.2f}"
                        ),
                        stop_loss=True,
                    )
                )

    # Calculate sector exposure
    sector_current: dict[str, float] = {}
    for p in current_positions:
        sym = p["symbol"]
        sec = _sector_of(sym)
        val = p.get("current_value", 0) or (p["shares"] * p["avg_cost"])
        sector_current[sec] = sector_current.get(sec, 0) + val

    sector_pcts = {sec: _pct(val, total_before) for sec, val in sector_current.items()}

    # 5. Apply sector cap — force sells on over-represented sectors.
    #     Uses tiered per-sector caps (config.sector_cap) with momentum uplift,
    #     so a winning sector (e.g. banking trending up) is NOT force-trimmed at
    #     HARD; only fading/over-weight sectors get trimmed. Risk-bounded, not
    #     winner-punishing.
    for sec, pct in sector_pcts.items():
        cap = config.sector_cap(sec)
        hard = cap["hard"]
        if pct > hard:
            excess_pct = pct - hard
            excess_val = total_before * (excess_pct / 100)
            # Find holdings in this sector, biggest first
            sector_holdings = sorted(
                [p for p in current_positions if _sector_of(p["symbol"]) == sec],
                key=lambda x: -(x.get("current_value", 0) or x["shares"] * x["avg_cost"]),
            )
            remaining = excess_val
            for p in sector_holdings:
                if remaining <= 0:
                    break
                sym = p["symbol"]
                # If already in sell list, skip (already being reduced)
                if any(s["symbol"] == sym for s in sell_list):
                    continue
                current_val = p.get("current_value", 0) or p["shares"] * p["avg_cost"]
                # Calculate how many shares to sell to bring sector under cap
                sell_fraction = min(remaining / current_val, 1.0) if current_val > 0 else 0
                sell_list.append(
                    dict(
                        symbol=sym,
                        delta_shares=0,  # sized in step 7 via sector_cap path
                        target_pct=_pct(current_val - remaining, total_before),
                        reason=f"Sector cap: {sec} at {pct:.0f}% exceeds {hard:.0f}% limit",
                        sector_cap=True,
                        excess_pct=pct - hard,
                    )
                )
                remaining -= current_val

    # 6. 🟢 Gap Risk Filter — skip trades where a price gap makes the signal stale
    #    Calls Mansa quote endpoint per candidate (not movers list) so every
    #    trade candidate is evaluated regardless of whether it's a top mover.
    #    - Gap UP > threshold → skip buy (avoid chasing stale signal)
    #    - Gap DOWN > threshold → skip sell (avoid panic at open low)
    #    Per-stock thresholds from config.GAP_THRESHOLDS override the default.
    #    Stop-loss and sector-cap sells are never filtered.
    if os.environ.get("MANSA_API_KEY"):
        import urllib.request as _ur

        _mansa_key = os.environ.get("MANSA_API_KEY", "")
        if _mansa_key:
            # Build candidate set from unique symbols in buy_list + sell_list
            _candidate_symbols = sorted(
                set([b["symbol"] for b in buy_list] + [s["symbol"] for s in sell_list])
            )

            _gap_map: dict[str, float] = {}
            _gap_diff_log: dict[str, dict] = {}
            _gap_unavailable: list[str] = []

            # Per-symbol Mansa quote calls (replaces old movers endpoint)
            for sym in _candidate_symbols:
                try:
                    url = f"https://mansaapi.com/api/v1/markets/exchanges/KENYA/stocks/{sym}"
                    req = _ur.Request(
                        url, headers={"Authorization": f"Bearer {_mansa_key}"}
                    )
                    with _ur.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode())
                    if data.get("success"):
                        price = data["data"].get("price")
                        if price and 1 < float(price) < 1000:
                            _gap_map[sym] = float(price)
                except Exception:
                    _gap_unavailable.append(sym)
                    continue

            # Evaluate each candidate
            for sym in _candidate_symbols:
                if sym in _gap_unavailable:
                    continue
                price_now = _gap_map.get(sym)
                if price_now is None:
                    continue
                # Get yesterday's close from CSV
                try:
                    csv_path = Path(config.HOME) / "data" / f"nse_{sym}.csv"
                    if csv_path.exists():
                        import csv

                        with open(csv_path) as f:
                            rows = list(csv.DictReader(f))
                        if rows:
                            _last_date = rows[-1].get("date", "").strip()
                            _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                            if _last_date == _today and len(rows) >= 2:
                                yesterday = rows[-2]
                            elif _last_date == _today:
                                continue  # No prior-day data — can't check gap
                            else:
                                yesterday = rows[-1]
                        close_yest = float(yesterday.get("close", yesterday.get("Close", 0)))
                    else:
                        continue
                except Exception:
                    continue
                # Compute gap %
                gap_pct = (price_now - close_yest) / close_yest * 100
                # Get per-stock threshold (else default)
                threshold = config.GAP_THRESHOLDS.get(sym, config.GAP_THRESHOLD_PCT)
                # Determine if we are considering a buy or sell for this symbol
                # (from buy_list and sell_list)
                is_buy_candidate = any(b["symbol"] == sym for b in buy_list)
                is_sell_candidate = any(s["symbol"] == sym for s in sell_list)
                # Skip buys on gap up, skip sells on gap down
                if is_buy_candidate and gap_pct > threshold:
                    # Skip this buy
                    for b in buy_list:
                        if b["symbol"] == sym:
                            b["_skip_gap"] = True
                            break
                if is_sell_candidate and gap_pct < -threshold:
                    # Skip this sell
                    for s in sell_list:
                        if s["symbol"] == sym:
                            s["_skip_gap"] = True
                            break

    # 7. Execute sells first (to free cash for buys)
    # We'll use a shared ExecutionEngine for live trades (non-dry_run)
    if not dry_run:
        # Create a shared ExecutionEngine for the whole run
        order_store = OrderStore(store_dir=os.path.join(config.HOME, "execution"))
        engine = ExecutionEngine(
            broker=PaperBroker(portfolio_dir=str(portfolio_dir)),
            safety=safety,
            order_store=order_store,
            alerts_path=os.path.expanduser("~/.trading/execution/alerts.log"),
            broker_timeout=float(config.EXECUTION_CONFIG.get("broker_timeout", 10.0)),
            max_retries=int(config.EXECUTION_CONFIG.get("max_retries", 2)),
            # Real scheduled trades are allowed to write to the live ledger.
            production=True,
        )
        engine.connect()

    for sell in sell_list:
        if sell.get("_skip_gap"):
            sell["reason"] = sell["reason"] + " [skipped: gap down]"
            report.add_skip(sell["symbol"], sell["reason"])
            continue
        sym = sell["symbol"]
        delta = sell["delta_shares"]
        # If delta_shares is 0 (set by sector-cap path), compute the shares to sell
        if delta == 0:
            # Compute shares to sell to reduce sector exposure
            current_val = holdings.get(sym, 0) * prices.get(sym, 0)
            target_val = (total_before * sell["target_pct"]) / 100
            sell_shares = max(
                0, int((current_val - target_val) / prices.get(sym, 1))
            ) if prices.get(sym, 0) > 0 else 0
        else:
            sell_shares = delta
        if sell_shares <= 0:
            continue
        price = prices.get(sym)
        if not price or price <= 0:
            report.add_skip(sym, "No price data")
            continue
        sell_value = sell_shares * price
        if sell_value < config.MIN_TRADE_KES:
            report.add_skip(sym, f"Sell too small (KES {sell_value:,.0f})")
            continue

        # Safety on sells (emergency stop / manual block / trade size)
        order = OrderRequest(
            symbol=sym, side="SELL", quantity=sell_shares, price=price, reason=sell["reason"]
        )
        acct = _account_info(cash, total_before, cash, len(holdings))
        port_state = _port_state_for_safety(current_positions)
        verdict = safety.check_order(order, port_state, acct)
        if not verdict.allowed:
            report.add_skip(sym, f"Safety blocked: {verdict.reason}")
            continue

        if dry_run:
            report.add_sell(sym, sell_shares, price, sell_value, sell["reason"])
            # Simulate cash free-up for subsequent buy sizing in dry-run
            fee_est = max(round(sell_value * (config.FEE_HEADROOM - 1.0), 2), 0.01)
            cash = round(cash + sell_value - fee_est, 2)
            holdings[sym] = holdings.get(sym, 0) - sell_shares
            if holdings[sym] <= 0:
                holdings.pop(sym, None)
        else:
            # Live trade via ExecutionEngine
            try:
                report_engine = engine.execute(order)
                if report_engine.success:
                    report.add_sell(sym, sell_shares, price, sell_value, sell["reason"])
                    # Update cash and holdings from the broker after the trade
                    acct_after = engine.broker.get_account()
                    positions_after = engine.broker.get_positions()
                    cash = acct_after.cash
                    # Rebuild holdings dict from positions
                    holdings.clear()
                    for p in positions_after:
                        holdings[p.symbol] = p.quantity
                else:
                    report.add_skip(sym, f"Sell failed: {report_engine.message}")
                    # Track UNKNOWN orders for force-reconcile on the next run
                    # (paper broker worker may have completed after the timeout).
                    if "UNKNOWN" in (report_engine.message or ""):
                        _record_pending_reconcile(sym, "SELL", sell_shares, price)
            except Exception as exc:
                report.add_skip(sym, f"Sell failed: {exc}")

    # 8. Refresh state after sells
    if not dry_run:
        state = _load_state(portfolio_dir)
        cash = state.get("cash", 0.0)
        current_positions = state.get("positions", [])
        holdings = {p["symbol"]: p["shares"] for p in current_positions}
    else:
        # Rebuild positions list values for sector math after simulated sells
        current_positions = [
            p for p in current_positions if holdings.get(p["symbol"], 0) > 0
        ]
        for p in current_positions:
            p["shares"] = holdings.get(p["symbol"], 0)

    # 9. Execute buys — plan qty is ALWAYS a delta (shares to buy now)
    cash_reserve = total_before * (config.CASH_RESERVE_PCT / 100)
    max_deploy_today = total_before * (config.DAILY_DEPLOYMENT_CAP_PCT / 100)
    available_cash = min(max(0.0, cash - cash_reserve), max_deploy_today)
    # Track post-sell sector values so room-in-sector math reflects simulated sells
    sector_after_sells: dict[str, float] = {}

    for buy in buy_list:
        if buy.get("_skip_gap"):
            buy["reason"] = buy["reason"] + " [skipped: gap up]"
            report.add_skip(buy["symbol"], buy["reason"])
            continue
        sym = buy["symbol"]
        delta = buy["delta_shares"]
        if delta <= 0:
            report.add_skip(sym, "Zero delta shares from plan")
            continue
        price = prices.get(sym)
        if not price or price <= 0:
            report.add_skip(sym, "No price data")
            continue
        sec = _sector_of(sym)
        current_sec_val = sector_after_sells.get(sec, 0) if 'sector_after_sells' in locals() else 0
        cap = config.sector_cap(sec)
        max_sector_val = total_before * (cap["warn"] / 100)
        room_in_sector = max_sector_val - current_sec_val
        if room_in_sector <= config.MIN_TRADE_KES:
            report.add_skip(
                sym,
                f"Sector cap: {sec} already at or near {cap['warn']:.0f}% limit. "
                f"Room left: KES {room_in_sector:,.0f}",
            )
            continue
        max_single = total_before * 0.25
        desired_value = delta * price
        cash_for_trade = min(desired_value, available_cash, room_in_sector, max_single)
        if cash_for_trade < config.MIN_TRADE_KES:
            report.add_skip(sym, f"Not enough cash (KES {cash_for_trade:,.0f} available)")
            continue
        buy_shares = min(delta, int(cash_for_trade / price))
        # Reserve headroom for transaction fee
        while buy_shares > 0 and (buy_shares * price) * config.FEE_HEADROOM > available_cash + 0.0001:
            buy_shares -= 1
        buy_value = buy_shares * price
        if buy_shares <= 0 or buy_value < config.MIN_TRADE_KES:
            report.add_skip(sym, f"Trade too small after calculation (KES {buy_value:,.0f})")
            continue

        order = OrderRequest(
            symbol=sym, side="BUY", quantity=buy_shares, price=price, reason=buy["reason"]
        )
        acct = _account_info(
            cash, total_before, available_cash, len(holdings)
        )
        port_state = _port_state_for_safety(current_positions)
        if sym not in port_state["positions"]:
            port_state["positions"][sym] = {"value": 0.0}
        verdict = safety.check_order(order, port_state, acct)
        if not verdict.allowed:
            report.add_skip(sym, f"Safety blocked: {verdict.reason}")
            continue

        if dry_run:
            report.add_buy(sym, buy_shares, price, buy_value, buy["reason"])
            fee_est = max(round(buy_value * (config.FEE_HEADROOM - 1.0), 2), 0.01)
            available_cash -= buy_value + fee_est
            cash -= buy_value + fee_est
            sector_after_sells[sec] = current_sec_val + buy_value
            holdings[sym] = holdings.get(sym, 0) + buy_shares
        else:
            # Live trade via ExecutionEngine
            try:
                report_engine = engine.execute(order)
                if report_engine.success:
                    report.add_buy(sym, buy_shares, price, buy_value, buy["reason"])
                    # Update cash and holdings from the broker after the trade
                    acct_after = engine.broker.get_account()
                    positions_after = engine.broker.get_positions()
                    cash = acct_after.cash
                    # Rebuild holdings dict from positions
                    holdings.clear()
                    for p in positions_after:
                        holdings[p.symbol] = p.quantity
                else:
                    report.add_skip(sym, f"Buy failed: {report_engine.message}")
                    # Track UNKNOWN orders for force-reconcile on the next run.
                    if "UNKNOWN" in (report_engine.message or ""):
                        _record_pending_reconcile(sym, "BUY", buy_shares, price)
            except Exception as exc:
                report.add_skip(sym, f"Buy failed: {exc}")

    # 10. Final state refresh (for dry-run we already have updated state)
    if not dry_run:
        state = _load_state(portfolio_dir)
        cash = state.get("cash", 0.0)
        current_positions = state.get("positions", [])
    else:
        # Already updated in the buy/sell loops
        pass

    # Build final portfolio after
    invested_after = sum(
        p.get("current_value", 0) or (p["shares"] * p["avg_cost"])
        for p in current_positions
    )
    total_after = cash + invested_after
    report.set_portfolio(
        report.portfolio_before,
        {
            "cash": cash,
            "total_value": total_after,
            "initial_capital": initial_capital,
            "positions": current_positions,
        },
    )

    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the auto-trader")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no trades executed)",
    )
    args = parser.parse_args()
    # Live runs take a RunLock to prevent a second concurrent live run
    # (cron overlap, stuck Mansa call) from double-filling. Dry-run and
    # replay are read-only / sandbox-isolated, so they don't take the lock.
    if not args.dry_run and not getattr(args, "replay_date", None):
        lock = RunLock(holder="auto-trader")
        if not lock.acquire():
            print(
                "🔒 Auto-Trader already running (lock held) — refusing to start a "
                "second live run to prevent double-fill. Exiting."
            )
            sys.exit(0)
        try:
            report = run_auto_trade(dry_run=args.dry_run)
        finally:
            lock.release()
    else:
        report = run_auto_trade(dry_run=args.dry_run)
    print(report.build())