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
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the project root is on the path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from trading.portfolio import engine as port_engine
from trading.execution.safety import SafetyEngine
from trading.execution.models import OrderRequest, OrderResult, AccountInfo
from trading.execution.run_lock import RunLock
from trading.execution.order_store import OrderStore
from trading.execution.alerting import alert
from trading import config as _cfg_mod

# ── Config ──────────────────────────────────────────────────────────────────
# Sector hard cap is derived from strategy (target + tolerance); fallback 50%.
try:
    from trading.target_allocation import max_sector_exposure_pct as _max_sec
    MAX_SECTOR_EXPOSURE_PCT = float(_max_sec())
except Exception:
    MAX_SECTOR_EXPOSURE_PCT = 50.0

from trading import replay as _replay

CASH_RESERVE_PCT = 10.0  # keep at least 10% of *portfolio* in cash
DAILY_DEPLOYMENT_CAP_PCT = 25.0  # max % of total portfolio to deploy per day
MIN_TRADE_KES = 1_000.0
STOP_LOSS_PCT = 8.0      # auto-sell if a position drops this % below avg cost
FEE_HEADROOM = 1.0 + float(getattr(port_engine, "TRANSACTION_FEE_PCT", 0.01))

# NSE names with trading halted (regulatory suspension / mandatory offer /
# delisting). The engine must never auto-trade these — they cannot be exited
# and any price is event-driven, not a tradeable signal.
SUSPENDED_SYMBOLS: frozenset[str] = frozenset({"BAMB"})

# Conviction weights kept for reporting/compat (sizing is plan-delta driven)
CONVICTION_WEIGHT = {
    "strong": 3.0,
    "moderate": 2.0,
    "weak": 1.0,
}

DECISION_CACHE_DIR = Path(os.path.expanduser("~/.trading/cache"))


def _make_safety() -> SafetyEngine:
    """SafetyEngine wired to EXECUTION_CONFIG (not bare defaults)."""
    cfg = dict(_cfg_mod.EXECUTION_CONFIG)
    # Ensure state_dir under ~/.trading/execution
    cfg.setdefault("state_dir", os.path.expanduser("~/.trading/execution"))
    cfg.setdefault(
        "emergency_stop_path",
        os.path.expanduser("~/.trading/execution/EMERGENCY_STOP"),
    )
    return SafetyEngine(cfg)


def _current_drawdown_pct(portfolio_dir: Optional[Path] = None) -> float:
    """Max drawdown % across the portfolio's MTM equity-curve snapshots.

    Reads snapshots.json directly (the same source portfolio_mtm uses) so the
    value is always the real peak-to-current drawdown, independent of any
    cached safety state.
    """
    try:
        from trading.portfolio import engine as pf
        snaps = pf.load_snapshots(portfolio_dir)
        if not snaps:
            return 0.0
        dds = pf.compute_drawdown(snaps)
        return max(dds) if dds else 0.0
    except Exception:
        return 0.0


_MACRO_SNAPSHOT_PATH = Path(os.path.expanduser("~/.trading/execution/macro_snapshot.json"))


def _port_state_for_safety(positions: list[dict]) -> dict:
    return {
        "positions": {
            p["symbol"]: {
                "value": p.get("current_value", 0) or p["shares"] * p["avg_cost"]
            }
            for p in positions
        }
    }


def _account_info(
    cash: float, equity: float, buying_power: float, n_pos: int
) -> AccountInfo:
    return AccountInfo(
        cash=cash,
        equity=equity,
        buying_power=buying_power,
        positions_count=n_pos,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
    )


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


# Phase 0 reconciliation — verify an executed trade's fill against intent.
# Paper fills are exact, but on a real broker a fill can be partial or at a
# slipped price; this catches it and alerts so a silent short-fill is never
# missed. Tolerance mirrors the ExecutionEngine (0.5%).
_RECON_PRICE_TOL_PCT = 0.5


def _reconcile_trade(
    symbol: str, side: str, intended_shares: int, intended_price: float,
    txn, *, alerts_path: str | None = None,
) -> list[str]:
    """Compare the executed transaction to what was intended.

    Returns a list of mismatch descriptions (empty == clean). Alerts on any
    mismatch with CRITICAL severity (money/order-state risk).
    """
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
            context={"symbol": symbol, "side": side,
                     "intended_shares": intended_shares,
                     "intended_price": intended_price},
            alerts_path=alerts_path,
        )
    return mismatches


# ── Reporter ────────────────────────────────────────────────────────────────

# ── Helpers ─────────────────────────────────────────────────────────────────
SECTOR_MAP: dict[str, str] = dict(_cfg_mod.SECTOR_MAP)


def _load_state(portfolio_dir: Path) -> dict[str, Any]:
    """Load the current portfolio state."""
    state_path = portfolio_dir / "state.json"
    if not state_path.exists():
        return {"cash": 0.0, "positions": [], "initial_capital": 0.0}
    with open(state_path) as f:
        return json.load(f)


def _mystocks_price(symbol: str) -> float | None:
    """Try to get today's 15-min delayed price from mystocks cache."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cache_path = DECISION_CACHE_DIR / f"live-prices-{today}.json"
        if cache_path.exists():
            with open(cache_path) as f:
                data = json.load(f)
            price = data.get("stocks", {}).get(symbol)
            if price and 1 < price < 1000:
                return float(price)
    except Exception:
        pass
    return None


def _mansa_price(symbol: str) -> float | None:
    """Try to get current price from Mansa API (primary source)."""
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
                return float(price)
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
        from trading import config
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
    return _cfg_mod.get_sector(symbol)


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
        return "[REPLAY] " if _replay.is_replay() else ""

    def add_buy(self, symbol: str, shares: int, price: float, value: float,
                reason: str) -> None:
        self.stocks_bought.append(dict(
            symbol=symbol, shares=shares, price=price, value=value, reason=reason,
        ))

    def add_sell(self, symbol: str, shares: int, price: float, value: float,
                 reason: str) -> None:
        self.stocks_sold.append(dict(
            symbol=symbol, shares=shares, price=price, value=value, reason=reason,
        ))

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
                    buf.append(f"  **{t['symbol']}**: {t['shares']} shares @ KES {t['price']:,.2f} "
                               f"= KES {t['value']:,.0f}")
                    buf.append(f"    → {t['reason']}")
                buf.append("")

            if self.stocks_sold:
                buf.append("━━━ 🔴 Sold ━━━")
                for t in self.stocks_sold:
                    buf.append(f"  **{t['symbol']}**: {t['shares']} shares @ KES {t['price']:,.2f} "
                               f"= KES {t['value']:,.0f}")
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
                buf.append(f"  {sym:<5s}  {shares:>4d} shares @ KES {cost:<8.2f}  "
                           f"valued at KES {total_vals:>8,.0f}  ({sector})")
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
            if pct > MAX_SECTOR_EXPOSURE_PCT:
                over_limit.append((sec, pct))

        if over_limit:
            buf.append("━━━ ⚠️ Sector Warning ━━━")
            for sec, pct in over_limit:
                buf.append(f"  {sec} at {pct:.0f}% exceeds the {MAX_SECTOR_EXPOSURE_PCT:.0f}% limit")
            buf.append("  Consider diversifying into other sectors on next trade day.")
            buf.append("")

        return "\n".join(buf)


# ── Auto-Trader ─────────────────────────────────────────────────────────────
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
    report = AutoTraderReport()

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
                f"(>= {dd_status['limit']:.2f}%) — no new trades",
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
    if _replay.is_replay():
        _replay.ensure_replay_env()
        portfolio_dir = _replay.sandbox_portfolio_dir()
        _replay.bootstrap_sandbox(portfolio_dir)
        report.add_skip("REPLAY", f"Sandbox portfolio dir: {portfolio_dir}")
    else:
        portfolio_dir = Path(os.path.expanduser("~/.trading/portfolio"))
    state_path = portfolio_dir / "state.json"
    state = _load_state(portfolio_dir)
    initial_capital = state.get("initial_capital", 100_000.0)
    cash = state.get("cash", 0.0)
    current_positions = state.get("positions", [])

    # 1b. Engine agreement gate (defense-in-depth).
    # Verify the live rebalance engine (target_allocation) and the Decision
    # Engine (services.decision) agree on per-stock equity targets. In
    # nse_only mode they share a single source of truth, so divergence
    # beyond tolerance indicates a real regression. FAIL-OPEN: any error in
    # the verification itself must never block trading — we treat it as
    # agreed and move on.
    try:
        from trading.target_allocation import verify_target_agreement
        agree = verify_target_agreement(nse_only=True)
        if not agree.get("agreed", True):
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
            "ENGINE-AGREEMENT",
            f"verification skipped (non-fatal): {exc}",
        )

    # Drop suspended/halted names from the auto-trade universe. They remain in
    # state.json as a manual hold; the engine simply refuses to BUY/SELL them.
    for p in list(current_positions):
        if p["symbol"] in SUSPENDED_SYMBOLS:
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
    invested = sum(p.get("current_value", 0) or (p["shares"] * p["avg_cost"])
                   for p in current_positions)
    total_before = cash + invested

    report.set_portfolio({
        "cash": cash,
        "total_value": total_before,
        "initial_capital": initial_capital,
        "positions": current_positions,
    }, {})

    # 2. Get target-based rebalance plan (strategic sector allocation + signal gating)
    #    SHARES CONTRACT: plan emits delta_shares (shares to trade NOW).
    #    Never interpret plan qty as an absolute target holding.
    buy_list: list[dict] = []
    sell_list: list[dict] = []
    try:
        from trading.target_allocation import generate_rebalance_plan
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
                buy_list.append(dict(
                    symbol=t["symbol"],
                    delta_shares=delta,
                    conviction="moderate",
                    reason=t["reason"],
                ))
            elif t["side"] == "SELL":
                sell_list.append(dict(
                    symbol=t["symbol"],
                    delta_shares=delta,
                    reason=t["reason"],
                    stop_loss=False,
                    sector_cap=False,
                ))
        if not buy_list and not sell_list:
            report.add_skip("ALL", "Portfolio is balanced — no trades from target allocation")
    except Exception as exc:
        report.add_skip("ALL", f"Target allocation engine failed: {exc}")

    # 3. Get current prices for all symbols we care about
    all_symbols = sorted(set(
        list(holdings.keys())
        + [b["symbol"] for b in buy_list]
        + [s["symbol"] for s in sell_list]
    ))
    prices = _price_map(all_symbols)

    # 4. Stop-loss check: auto-sell positions that hit the loss threshold.
    #    Uses the SAME helper the safety gate uses (safety.should_stop_loss),
    #    so the auto-trader and manual CLI never disagree on what "stopped"
    #    means. Do NOT report sells here — report only after execution (step 7).
    sl_portfolio_state = {
        "positions": {
            p["symbol"]: {
                "shares": p["shares"],
                "avg_cost": p["avg_cost"],
                # value uses the live price if available, else current_value
                "value": (prices.get(p["symbol"]) or 0.0)
                * p["shares"]
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
                sell_list.append(dict(
                    symbol=sym,
                    delta_shares=int(p["shares"]),
                    reason=(
                        f"Stop-loss triggered: {sym} is {sl['loss_pct']:.1f}% "
                        f"below avg cost of KES {sl['avg_cost']:.2f}"
                    ),
                    stop_loss=True,
                ))

    # Calculate sector exposure
    sector_current: dict[str, float] = {}
    for p in current_positions:
        sym = p["symbol"]
        sec = _sector_of(sym)
        val = p.get("current_value", 0) or (p["shares"] * p["avg_cost"])
        sector_current[sec] = sector_current.get(sec, 0) + val

    sector_pcts = {sec: _pct(val, total_before) for sec, val in sector_current.items()}

    # 5. Apply sector cap — force sells on over-represented sectors
    for sec, pct in sector_pcts.items():
        if pct > MAX_SECTOR_EXPOSURE_PCT:
            excess_pct = pct - MAX_SECTOR_EXPOSURE_PCT
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
                sell_list.append(dict(
                    symbol=sym,
                    delta_shares=0,  # sized in step 7 via sector_cap path
                    target_pct=_pct(current_val - remaining, total_before),
                    reason=f"Sector cap: {sec} at {pct:.0f}% exceeds {MAX_SECTOR_EXPOSURE_PCT:.0f}% limit",
                    sector_cap=True,
                    excess_pct=pct - MAX_SECTOR_EXPOSURE_PCT,
                ))
                remaining -= p.get("current_value", 0) or p["shares"] * p["avg_cost"]

    # 6. 🟢 Gap Risk Filter — skip trades where a price gap makes the signal stale
    #    Calls Mansa quote endpoint per candidate (not movers list) so every
    #    trade candidate is evaluated regardless of whether it's a top mover.
    #    - Gap UP > threshold → skip buy (avoid chasing stale signal)
    #    - Gap DOWN > threshold → skip sell (avoid panic at open low)
    #    Per-stock thresholds from config.GAP_THRESHOLDS override the default.
    #    Stop-loss and sector-cap sells are never filtered.
    from trading import config as _cfg
    import urllib.request as _ur

    _mansa_key = os.environ.get("MANSA_API_KEY", "")
    if _mansa_key:
        # Build candidate set from unique symbols in buy_list + sell_list
        _candidate_symbols = sorted(set(
            [b["symbol"] for b in buy_list] + [s["symbol"] for s in sell_list]
        ))

        _gap_map: dict[str, float] = {}
        _gap_diff_log: dict[str, dict] = {}
        _gap_unavailable: list[str] = []

        # Per-symbol Mansa quote calls (replaces old movers endpoint)
        _today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for _sym in _candidate_symbols:
            try:
                _url = f"https://mansaapi.com/api/v1/markets/exchanges/KENYA/stocks/{_sym}"
                _req = _ur.Request(_url, headers={"Authorization": f"Bearer {_mansa_key}"})
                with _ur.urlopen(_req, timeout=10) as _resp:
                    _data = json.loads(_resp.read().decode())
                if _data.get("success"):
                    _ma_price = float(_data["data"].get("price", 0))
                    if _ma_price and 1 < _ma_price < 1000:
                        # Compute gap from yesterday's CSV close
                        _csv_path = Path(_cfg.HOME) / "data" / f"nse_{_sym}.csv"
                        if _csv_path.exists():
                            import csv as _csv_mod
                            with open(_csv_path) as _f_csv:
                                _rows = list(_csv_mod.DictReader(_f_csv))
                            if _rows:
                                _last_date = _rows[-1].get("date", "").strip()
                                if _last_date == _today_str and len(_rows) >= 2:
                                    _prev = float(_rows[-2].get("close", _rows[-2].get("Close", 0)))
                                elif _last_date == _today_str:
                                    _prev = 0.0
                                else:
                                    _prev = float(_rows[-1].get("close", _rows[-1].get("Close", 0)))
                                if _prev > 0:
                                    _gap_pct = (_ma_price - _prev) / _prev * 100
                                    _gap_map[_sym] = round(_gap_pct, 2)
                if _sym not in _gap_map:
                    _gap_unavailable.append(_sym)
            except Exception:
                _gap_unavailable.append(_sym)

        # Cross-check with mystocks cache (same logic, now per candidate)
        if _gap_map:
            try:
                _ms_path = DECISION_CACHE_DIR / f"live-prices-{_today_str}.json"
                if _ms_path.exists():
                    with open(_ms_path) as _f_ms:
                        _ms_data = json.load(_f_ms)
                    _ms_prices = _ms_data.get("stocks", {})
                    for _sym in list(_gap_map.keys()):
                        _ms_p = _ms_prices.get(_sym)
                        if _ms_p and 1 < float(_ms_p) < 1000:
                            _ms_p = float(_ms_p)
                            _csv_path = Path(_cfg.HOME) / "data" / f"nse_{_sym}.csv"
                            if _csv_path.exists():
                                import csv as _csv_mod
                                with open(_csv_path) as _f_csv:
                                    _rows = list(_csv_mod.DictReader(_f_csv))
                                if _rows:
                                    # Old (buggy) vs New (fixed) date computation
                                    _prev_old = float(_rows[-1].get("close", _rows[-1].get("Close", 0)))
                                    _ms_gap_old = (_ms_p - _prev_old) / _prev_old * 100 if _prev_old > 0 else 0.0

                                    _last_date = _rows[-1].get("date", "").strip()
                                    if _last_date == _today_str and len(_rows) >= 2:
                                        _prev = float(_rows[-2].get("close", _rows[-2].get("Close", 0)))
                                    elif _last_date == _today_str:
                                        _prev = 0.0
                                    else:
                                        _prev = float(_rows[-1].get("close", _rows[-1].get("Close", 0)))

                                    if _prev > 0:
                                        _ms_gap = (_ms_p - _prev) / _prev * 100
                                        _ma_gap = _gap_map[_sym]

                                        # Log old-vs-new diff when they diverge
                                        if abs(_ms_gap - _ms_gap_old) > 0.01:
                                            _gap_diff_log[_sym] = {
                                                "old_prev": _prev_old,
                                                "new_prev": _prev,
                                                "old_gap_pct": round(_ms_gap_old, 2),
                                                "new_gap_pct": round(_ms_gap, 2),
                                                "fixed": _prev != _prev_old,
                                            }

                                        # If both Mansa and mystocks show same direction, use smaller gap
                                        if _ma_gap > 0 and _ms_gap > 0:
                                            _gap_map[_sym] = min(_ma_gap, _ms_gap)
                                        elif _ma_gap < 0 and _ms_gap < 0:
                                            _gap_map[_sym] = max(_ma_gap, _ms_gap)
            except Exception:
                pass  # mystocks cross-check is best-effort

        # 6a. Apply gap filter to buy/sell lists
        if _gap_map:
            _filtered_buys = []
            for b in buy_list:
                sym = b["symbol"]
                g = _gap_map.get(sym, 0)
                _t = _cfg.GAP_THRESHOLDS.get(sym, _cfg.GAP_THRESHOLD_PCT)
                if g > _t:
                    report.add_skip(sym, f"Gap up {g:+.1f}% exceeds {_t:.0f}% threshold — signal stale, skipping")
                else:
                    _filtered_buys.append(b)
            buy_list = _filtered_buys

            _filtered_sells = []
            for s in sell_list:
                sym = s["symbol"]
                # Never override stop-loss or sector-cap sells
                if s.get("stop_loss") or s.get("sector_cap"):
                    _filtered_sells.append(s)
                    continue
                g = _gap_map.get(sym, 0)
                _t = _cfg.GAP_THRESHOLDS.get(sym, _cfg.GAP_THRESHOLD_PCT)
                if g < -_t:
                    report.add_skip(sym, f"Gap down {g:+.1f}% exceeds {_t:.0f}% threshold — avoiding panic sell at open low")
                else:
                    _filtered_sells.append(s)
            sell_list = _filtered_sells

        # 6b. Emit gap verification: one line per candidate
        _gap_available = 0
        _gap_unavail_count = 0
        for _sym in _candidate_symbols:
            if _sym in _gap_map:
                _gap_available += 1
                _g = _gap_map[_sym]
                if _sym in _gap_diff_log:
                    _d = _gap_diff_log[_sym]
                    if _d["fixed"]:
                        report.add_skip(
                            f"{_sym} [GAP]",
                            f"prev: old={_d['old_prev']:.2f} new={_d['new_prev']:.2f}  "
                            f"gap: old={_d['old_gap_pct']:+.2f}% → {_d['new_gap_pct']:+.2f}%  "
                            f"Mansa={_g:+.2f}% (date-fix active)",
                        )
                    else:
                        report.add_skip(
                            f"{_sym} [GAP]",
                            f"gap {_g:+.2f}% via Mansa quote (date-mismatch N/A)",
                        )
                else:
                    report.add_skip(
                        f"{_sym} [GAP]",
                        f"gap {_g:+.2f}% via Mansa quote",
                    )
            else:
                _gap_unavail_count += 1
                report.add_skip(
                    f"{_sym} [GAP]",
                    "Mansa returned no data — gap check skipped",
                )

        if _candidate_symbols:
            _n_total = len(_candidate_symbols)
            report.add_skip(
                "GAP FILTER SUMMARY",
                f"Assessed {_n_total} candidates ({_gap_available} with Mansa data"
                + (f", {_gap_unavail_count} unavailable)" if _gap_unavail_count else ")"),
            )

    # 7. Execute sells first (frees up cash for buys)
    for sell in sell_list:
        sym = sell["symbol"]
        current_shares = holdings.get(sym, 0)
        if current_shares <= 0:
            report.add_skip(sym, "No shares to sell")
            continue

        price = prices.get(sym)
        if not price or price <= 0:
            report.add_skip(sym, f"No price data for {sym}")
            continue

        # Determine how many shares to sell (delta contract preferred)
        target_pct = sell.get("target_pct", 0) or 0
        target_val = total_before * (target_pct / 100) if target_pct > 0 else 0
        current_val = current_shares * price
        delta = int(sell.get("delta_shares") or 0)

        if sell.get("stop_loss"):
            sell_shares = current_shares if delta <= 0 else min(delta, current_shares)
        elif sell.get("sector_cap"):
            excess_pct = sell.get("excess_pct", 0)
            target_val = current_val * (1 - excess_pct / 100)
            sell_shares = max(1, int((current_val - target_val) / price))
            sell_shares = min(sell_shares, current_shares)
        elif delta > 0:
            sell_shares = min(delta, current_shares)
        elif target_val > 0 and target_val < current_val:
            target_shares = max(1, int(target_val / price))
            sell_shares = current_shares - target_shares
            if sell_shares <= 0:
                report.add_skip(sym, f"Target ({target_pct:.1f}%) already met or exceeded")
                continue
        else:
            sell_shares = current_shares

        sell_value = sell_shares * price
        if sell_value < MIN_TRADE_KES:
            report.add_skip(sym, f"Sell too small (KES {sell_value:,.0f})")
            continue

        # Safety on sells (emergency stop / manual block / trade size)
        try:
            order = OrderRequest(
                symbol=sym, side="SELL", quantity=sell_shares,
                price=price, reason=sell["reason"],
            )
            acct = _account_info(cash, total_before, cash, len(holdings))
            verdict = safety.check_order(
                order, _port_state_for_safety(current_positions), acct,
            )
            if not verdict.allowed:
                report.add_skip(sym, f"Safety blocked: {verdict.reason}")
                continue
        except Exception as exc:
            report.add_skip(sym, f"Safety check failed: {exc}")
            continue

        if dry_run:
            report.add_sell(sym, sell_shares, price, sell_value, sell["reason"])
            # Simulate cash free-up for subsequent buy sizing in dry-run
            fee_est = max(round(sell_value * (FEE_HEADROOM - 1.0), 2), 0.01)
            cash = round(cash + sell_value - fee_est, 2)
            holdings[sym] = current_shares - sell_shares
            if holdings[sym] <= 0:
                holdings.pop(sym, None)
        else:
            try:
                _state, txn = port_engine.sell(
                    symbol=sym,
                    shares=sell_shares,
                    price=price,
                    reason=sell["reason"],
                    dir_path=str(portfolio_dir),
                )
                report.add_sell(sym, sell_shares, price, sell_value, sell["reason"])
                _record_fill(
                    safety,
                    side="SELL",
                    symbol=sym,
                    shares=sell_shares,
                    price=price,
                    fee=txn.fee,
                    realised_pnl=txn.realised_pnl,
                )
                # Phase 0 reconciliation: intended vs actual fill.
                _reconcile_trade(
                    sym, "SELL", sell_shares, price, txn,
                    alerts_path=os.path.expanduser("~/.trading/execution/alerts.log"),
                )
            except Exception as exc:
                report.add_skip(sym, f"Sell failed: {exc}")

    # 8. Refresh state after sells
    state = _load_state(portfolio_dir)
    if not dry_run:
        cash = state.get("cash", 0.0)
        current_positions = state.get("positions", [])
        holdings = {p["symbol"]: p["shares"] for p in current_positions}
    else:
        # Rebuild positions list values for sector math after simulated sells
        current_positions = [
            p for p in current_positions
            if holdings.get(p["symbol"], 0) > 0
        ]
        for p in current_positions:
            p["shares"] = holdings[p["symbol"]]

    sector_after_sells: dict[str, float] = {}
    for p in current_positions:
        sec = _sector_of(p["symbol"])
        sh = holdings.get(p["symbol"], p["shares"])
        px = prices.get(p["symbol"]) or p.get("avg_cost", 0)
        val = sh * px
        sector_after_sells[sec] = sector_after_sells.get(sec, 0) + val

    total_after_sells = cash + sum(sector_after_sells.values())

    # 9. Execute buys — plan qty is ALWAYS a delta (shares to buy now)
    cash_reserve = total_after_sells * (CASH_RESERVE_PCT / 100)
    max_deploy_today = total_after_sells * (DAILY_DEPLOYMENT_CAP_PCT / 100)
    available_cash = min(max(0.0, cash - cash_reserve), max_deploy_today)

    for buy in buy_list:
        sym = buy["symbol"]
        delta = int(buy.get("delta_shares") or 0)
        if delta <= 0:
            report.add_skip(sym, "Zero delta shares from plan")
            continue

        price = prices.get(sym)
        if not price or price <= 0:
            report.add_skip(sym, "No price data")
            continue

        sec = _sector_of(sym)
        current_sec_val = sector_after_sells.get(sec, 0)
        max_sector_val = total_after_sells * (MAX_SECTOR_EXPOSURE_PCT / 100)
        room_in_sector = max_sector_val - current_sec_val
        if room_in_sector <= MIN_TRADE_KES:
            report.add_skip(
                sym,
                f"Sector cap: {sec} already at or near {MAX_SECTOR_EXPOSURE_PCT:.0f}% limit. "
                f"Room left: KES {room_in_sector:,.0f}",
            )
            continue

        max_single = total_after_sells * 0.25
        desired_value = delta * price
        cash_for_trade = min(desired_value, available_cash, room_in_sector, max_single)
        if cash_for_trade < MIN_TRADE_KES:
            report.add_skip(sym, f"Not enough cash (KES {cash_for_trade:,.0f} available)")
            continue

        buy_shares = min(delta, int(cash_for_trade / price))
        # Reserve headroom for transaction fee
        while buy_shares > 0 and (buy_shares * price) * FEE_HEADROOM > available_cash + 0.0001:
            buy_shares -= 1
        buy_value = buy_shares * price
        if buy_shares <= 0 or buy_value < MIN_TRADE_KES:
            report.add_skip(sym, f"Trade too small after calculation (KES {buy_value:,.0f})")
            continue

        try:
            order = OrderRequest(
                symbol=sym, side="BUY", quantity=buy_shares,
                price=price, reason=buy["reason"],
            )
            acct = _account_info(
                cash, total_after_sells, available_cash, len(holdings),
            )
            # Include prospective position value for exposure check
            port_state = _port_state_for_safety(current_positions)
            if sym not in port_state["positions"]:
                port_state["positions"][sym] = {"value": 0.0}
            verdict = safety.check_order(order, port_state, acct)
            if not verdict.allowed:
                report.add_skip(sym, f"Safety blocked: {verdict.reason}")
                continue
        except Exception as exc:
            report.add_skip(sym, f"Safety check failed: {exc}")
            continue

        if dry_run:
            report.add_buy(sym, buy_shares, price, buy_value, buy["reason"])
            fee_est = max(round(buy_value * (FEE_HEADROOM - 1.0), 2), 0.01)
            available_cash -= buy_value + fee_est
            cash -= buy_value + fee_est
            sector_after_sells[sec] = current_sec_val + buy_value
            holdings[sym] = holdings.get(sym, 0) + buy_shares
        else:
            try:
                _state, txn = port_engine.buy(
                    symbol=sym,
                    shares=buy_shares,
                    price=price,
                    reason=buy["reason"],
                    dir_path=str(portfolio_dir),
                )
                report.add_buy(sym, buy_shares, price, buy_value, buy["reason"])
                _record_fill(
                    safety,
                    side="BUY",
                    symbol=sym,
                    shares=buy_shares,
                    price=price,
                    fee=txn.fee,
                )
                # Phase 0 reconciliation: intended vs actual fill.
                _reconcile_trade(
                    sym, "BUY", buy_shares, price, txn,
                    alerts_path=os.path.expanduser("~/.trading/execution/alerts.log"),
                )
                available_cash -= buy_value * FEE_HEADROOM
                cash = _state.cash
                sector_after_sells[sec] = current_sec_val + buy_value
                holdings[sym] = holdings.get(sym, 0) + buy_shares
                # Keep current_positions in sync for next safety exposure check
                found = False
                for p in current_positions:
                    if p["symbol"] == sym:
                        p["shares"] = holdings[sym]
                        p["current_value"] = holdings[sym] * price
                        found = True
                        break
                if not found:
                    current_positions.append({
                        "symbol": sym,
                        "shares": holdings[sym],
                        "avg_cost": price,
                        "total_cost": buy_value,
                        "current_value": buy_value,
                    })
            except port_engine.InsufficientCashError:
                report.add_skip(sym, "Insufficient cash after fees")
            except Exception as exc:
                report.add_skip(sym, f"Buy failed: {exc}")

    # 10. Final state + snapshot (live only)
    if not dry_run and (report.stocks_bought or report.stocks_sold):
        _maybe_snapshot(prices, portfolio_dir)

    final_state = _load_state(portfolio_dir)
    final_cash = final_state.get("cash", 0) if not dry_run else cash
    final_positions = final_state.get("positions", []) if not dry_run else [
        {
            "symbol": s,
            "shares": sh,
            "avg_cost": next(
                (p["avg_cost"] for p in current_positions if p["symbol"] == s),
                prices.get(s, 0),
            ),
            "current_value": sh * prices.get(s, 0),
        }
        for s, sh in holdings.items() if sh > 0
    ]
    invested_final = sum(
        p.get("current_value", 0) or p["shares"] * p.get("avg_cost", 0)
        for p in final_positions
    )
    total_after = final_cash + invested_final

    report.set_portfolio(
        {
            "cash": state.get("cash", cash) if not dry_run else total_after_sells - sum(sector_after_sells.values()),
            "total_value": total_after_sells,
            "initial_capital": initial_capital,
            "positions": current_positions,
        },
        {
            "cash": final_cash,
            "total_value": total_after,
            "initial_capital": initial_capital,
            "positions": final_positions,
        },
    )

    return report


# ── Entry point ─────────────────────────────────────────────────────────────
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Auto-trader — executes daily allocation in paper portfolio.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate only — don't execute trades.",
    )
    parser.add_argument(
        "--replay-date", default=None,
        help="Replay a historical date using archived data (YYYY-MM-DD).",
    )
    args = parser.parse_args()

    if args.replay_date:
        os.environ["REPLAY_DATE"] = args.replay_date

    print(f"🚀 Auto-Trader starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    mode = "REPLAY" if args.replay_date else ("DRY RUN" if args.dry_run else "LIVE")
    print(f"   Mode: {mode}" + (f" ({args.replay_date})" if args.replay_date else ""))
    print(f"   Schedule: 10:30 EAT (EOD-bar rebalance mid-session; gap-filtered)")
    print(f"   Sector cap: {MAX_SECTOR_EXPOSURE_PCT:.0f}%  Fee: {port_engine.TRANSACTION_FEE_PCT*100:.1f}% one-way")
    print()

    # ── Phase 0 run-lock ──
    # Prevent a second live run from overlapping a still-executing one (cron
    # overlap, slow data, hung Mansa call). Dry-run and replay are read-only /
    # sandbox-isolated, so they don't take the live lock.
    lock = RunLock(holder="auto-trader")
    if not args.dry_run and not args.replay_date:
        if not lock.acquire():
            print("🔒 Auto-Trader already running (lock held) — refusing to start a "
                  "second live run to prevent double-fill. Exiting.")
            return 0
    try:
        report = run_auto_trade(dry_run=args.dry_run or bool(args.replay_date))
    finally:
        if not args.dry_run and not args.replay_date:
            lock.release()
    print(report.build())

    # Refresh MTM prices in state.json after trades
    if not args.dry_run:
        import subprocess
        subprocess.run(
            ["python3", str(Path.home() / ".trading" / "scripts" / "refresh-mtm.py")],
            capture_output=True, text=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
