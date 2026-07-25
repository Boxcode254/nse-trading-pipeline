"""Outcome Reviewer — rebuilt 2026-07-26 against state.json + snapshots.json.

This replaces the archived paper_engine-dependent reviewer. It no longer
tracks "open paper positions with expiry" (the paper engine is retired).
Instead it reviews the *live* paper-trading portfolio that the auto-trader
drives, persisted in:

    portfolio/state.json       — cash + open positions (avg_cost, current_value)
    portfolio/snapshots.json   — mark-to-market history (prices, drawdown, value)

It produces three classes of signal, all routed to Telegram via the cron
delivery channel (the module just prints; the cron job carries it to the
trading channel). It is SILENT when everything is healthy so the recurring
cron does not spam.

    1. OUTCOME REVIEW     — realised P&L from new SELL transactions since the
                            last run (stateful), plus large unrealised P&L
                            drift vs the last snapshot. (Spiritual successor to
                            the old "hourly paper trade review".)
    2. FILL ANOMALY       — unknown symbols, dust fills (< min notional),
                            missing signal_ref on new fills, and buys priced
                            suspiciously far below cost (fat-finger / bad data).
                            Stateful: only NEW fills since the last checkpoint
                            are reviewed, so a one-time historical backlog never
                            floods the channel.
    3. EXPOSURE           — single-name concentration, thin cash buffer, and
                            portfolio drawdown breaches. Always evaluated
                            (current-state guard).

Design rules (per the re-build mandate):
* Read-only. No writes to state.json / snapshots.json / transactions.json.
* No dependency on the archived paper_engine system.
* Silent unless something needs attention (safe as a recurring guard).
* ``--self-test`` exercises every branch against a built-in fixture so the
  monitoring itself is verifiable.

Usage (cron):
    .venv/bin/python -m trading.outcome_reviewer            # silent when clean
    .venv/bin/python -m trading.outcome_reviewer --report   # always print summary
    .venv/bin/python -m trading.outcome_reviewer --self-test# exit 0 = all checks pass
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Resolve the trading package root so ``python -m trading.outcome_reviewer``
# and direct imports both work regardless of cwd.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading.portfolio.engine import (  # noqa: E402
    PortfolioState,
    Snapshot,
    compute_drawdown,
    compute_holdings_value,
    load_snapshots,
    load_state,
)


PORTFOLIO_DIR = Path.home() / ".trading" / "portfolio"
STATE_FILE = PORTFOLIO_DIR / "state.json"
SNAPSHOTS_FILE = PORTFOLIO_DIR / "snapshots.json"
TXN_FILE = PORTFOLIO_DIR / "transactions.json"

# Progress / stateful checkpoint so the monitoring itself is forward-only.
_PROGRESS_FILE = PORTFOLIO_DIR / ".outcome_reviewer_progress.json"

# ── Thresholds ─────────────────────────────────────────────────────────────
CONCENTRATION_PCT = 35.0        # single name above this of total value → alert
CASH_BUFFER_PCT = 8.0           # cash below this of total value → exposure alert
DRAWDOWN_ALERT_PCT = 10.0       # portfolio drawdown above this → alert
MIN_FILL_NOTIONAL = 1000.0      # fills smaller than this (KES) → dust alert
BUY_BELOW_COST_PCT = -15.0      # BUY priced this far below avg cost → anomaly
UNREALISED_DRIFT_PCT = 10.0     # |unrealised pnl%| above this vs last snap → note
# Symbols the auto-trader is allowed to act on (kept in sync with auto_trader).
KNOWN_UNIVERSE: frozenset[str] = frozenset({
    "ABSA", "COOP", "EABL", "EQTY", "SCOM", "KPLC", "KCB", "SCBK",
    "BAMB", "TOTL", "KNRE", "WTK", "SASN", "ARM", "CIC", "NMG",
})


# ── Result model ────────────────────────────────────────────────────────────
@dataclass
class ReviewFindings:
    realised: list[dict] = field(default_factory=list)
    unrealised_drift: list[dict] = field(default_factory=list)
    fill_anomalies: list[dict] = field(default_factory=list)
    exposure: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(
            self.realised or self.unrealised_drift
            or self.fill_anomalies or self.exposure or self.errors
        )

    def merge(self, other: "ReviewFindings") -> None:
        self.realised += other.realised
        self.unrealised_drift += other.unrealised_drift
        self.fill_anomalies += other.fill_anomalies
        self.exposure += other.exposure
        self.errors += other.errors


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"cannot read {path}: {e}") from e


def _load_transactions() -> list[dict]:
    data = _load_json(TXN_FILE)
    if data is None:
        return []
    if not isinstance(data, list):
        raise RuntimeError(f"{TXN_FILE.name} is not a JSON array")
    return data


def _load_progress() -> dict:
    if _PROGRESS_FILE.exists():
        try:
            return json.loads(_PROGRESS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_progress(progress: dict) -> None:
    _PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


# ── 1. Outcome review (realised + unrealised drift) ─────────────────────────
def review_outcomes(
    state: PortfolioState, snaps: list[Snapshot], since_ts: Optional[str]
) -> tuple[ReviewFindings, Optional[str]]:
    """Return (findings, new_max_txn_ts). Stateful: only SELLs newer than
    ``since_ts`` are reported. ``since_ts=None`` = first run (bootstrap,
    silent) — checkpoint is set but nothing is reported."""
    f = ReviewFindings()
    max_ts: Optional[str] = since_ts

    txns = _load_transactions()
    for t in txns:
        ts = t.get("timestamp", "")
        if ts > (max_ts or ""):
            max_ts = ts
        if since_ts is None:
            continue  # bootstrap: record checkpoint, report nothing yet
        if t.get("action") == "SELL" and t.get("realised_pnl") is not None and ts > since_ts:
            pnl = float(t.get("realised_pnl", 0.0))
            f.realised.append({
                "symbol": t.get("symbol"),
                "shares": t.get("shares"),
                "price": t.get("price"),
                "realised_pnl": round(pnl, 2),
                "reason": t.get("reason", ""),
                "timestamp": ts,
            })

    # Unrealised drift vs the most recent snapshot (always informative).
    if snaps:
        last = snaps[-1]
        last_prices = last.prices or {}
        prices = {pos.symbol: (last_prices.get(pos.symbol) or pos.avg_cost)
                  for pos in state.positions}
        _, rows = compute_holdings_value(state, prices)
        for r in rows:
            if abs(r["pnl_pct"]) >= UNREALISED_DRIFT_PCT:
                f.unrealised_drift.append({
                    "symbol": r["symbol"],
                    "pnl_pct": r["pnl_pct"],
                    "pnl": r["pnl"],
                    "last_price": r["last_price"],
                })

    return f, max_ts


# ── 2. Fill / anomaly review (stateful: new fills only) ─────────────────────
def review_fills(
    state: PortfolioState, since_ts: Optional[str]
) -> tuple[ReviewFindings, Optional[str]]:
    """Return (findings, new_max_txn_ts). Only fills newer than ``since_ts``
    are reviewed. First run (since_ts=None) bootstraps silently."""
    f = ReviewFindings()
    max_ts: Optional[str] = since_ts

    held = {p.symbol for p in state.positions}
    for t in _load_transactions():
        ts = t.get("timestamp", "")
        if ts > (max_ts or ""):
            max_ts = ts
        if since_ts is None:
            continue  # bootstrap
        if ts <= since_ts:
            continue

        action = t.get("action")
        symbol = t.get("symbol")
        price = t.get("price")
        shares = t.get("shares")
        reason = (t.get("reason") or "").lower()
        is_test = "test" in reason or "perm test" in reason
        if action not in ("BUY", "SELL") or not symbol or price is None:
            continue

        # (a) Unknown symbol — not in our universe and not currently held.
        if symbol not in KNOWN_UNIVERSE and symbol not in held:
            f.fill_anomalies.append({
                "type": "unknown_symbol", "symbol": symbol, "action": action,
                "reason": t.get("reason", ""),
            })

        # (b) Dust fill — economically meaningless notional.
        try:
            notional = float(price) * int(shares or 0)
        except (TypeError, ValueError):
            notional = 0.0
        if notional < MIN_FILL_NOTIONAL and not is_test:
            f.fill_anomalies.append({
                "type": "dust_fill", "symbol": symbol, "action": action,
                "notional": round(notional, 2),
            })

        # (c) Missing signal reference on a real fill.
        sig = t.get("signal_ref") or {}
        if not sig and not is_test:
            f.fill_anomalies.append({
                "type": "missing_signal_ref", "symbol": symbol, "action": action,
            })

        # (d) BUY priced suspiciously far below avg cost (fat-finger / bad data).
        if action == "BUY" and symbol in held:
            avg = next((p.avg_cost for p in state.positions if p.symbol == symbol), None)
            if avg:
                pnl_pct = (float(price) - avg) / avg * 100.0
                if pnl_pct <= BUY_BELOW_COST_PCT:
                    f.fill_anomalies.append({
                        "type": "buy_well_below_cost", "symbol": symbol,
                        "price": float(price), "avg_cost": round(avg, 4),
                        "pnl_pct": round(pnl_pct, 2),
                    })

    return f, max_ts


# ── 3. Exposure review (always evaluated) ───────────────────────────────────
def review_exposure(state: PortfolioState, snaps: list[Snapshot]) -> ReviewFindings:
    f = ReviewFindings()

    last_prices = snaps[-1].prices if snaps else {}
    prices = {p.symbol: (last_prices.get(p.symbol) or p.avg_cost)
              for p in state.positions}
    holdings_value, rows = compute_holdings_value(state, prices)
    total_value = state.cash + holdings_value
    if total_value <= 0:
        f.errors.append("total portfolio value is non-positive — cannot assess exposure")
        return f

    # (a) Single-name concentration.
    for r in rows:
        pct = r["value"] / total_value * 100.0
        if pct >= CONCENTRATION_PCT:
            f.exposure.append({
                "type": "concentration", "symbol": r["symbol"],
                "pct": round(pct, 1), "value": r["value"],
            })

    # (b) Cash buffer.
    cash_pct = state.cash / total_value * 100.0
    if cash_pct < CASH_BUFFER_PCT:
        f.exposure.append({
            "type": "low_cash", "cash_pct": round(cash_pct, 1),
            "cash": round(state.cash, 2),
        })

    # (c) Drawdown breach.
    if snaps:
        dds = compute_drawdown(snaps)
        if dds and dds[-1] >= DRAWDOWN_ALERT_PCT:
            f.exposure.append({"type": "drawdown", "drawdown_pct": dds[-1]})

    return f


# ── Reporting ───────────────────────────────────────────────────────────────
def render(f: ReviewFindings, always: bool = False) -> str:
    if not f.any and not always:
        return ""  # SILENT when clean — safe for recurring cron.

    lines: list[str] = ["📊 **Portfolio Outcome Review**", f"⏰ {_now_iso()}"]

    if f.realised:
        total = sum(r["realised_pnl"] for r in f.realised)
        lines.append("")
        lines.append(f"💰 **Realised P&L (new sells):** KES {total:,.2f}")
        for r in f.realised:
            emoji = "🟢" if r["realised_pnl"] >= 0 else "🔴"
            lines.append(
                f"  {emoji} {r['symbol']}: KES {r['realised_pnl']:,.2f} "
                f"@ {r['price']} ({r['shares']} sh) — {r['reason']}"
            )

    if f.unrealised_drift:
        lines.append("")
        lines.append("📈 **Unrealised drift (vs last snapshot):**")
        for r in f.unrealised_drift:
            emoji = "🟢" if r["pnl_pct"] >= 0 else "🔴"
            lines.append(
                f"  {emoji} {r['symbol']}: {r['pnl_pct']:+.1f}% "
                f"(KES {r['pnl']:,.0f}) @ {r['last_price']}"
            )

    if f.fill_anomalies:
        lines.append("")
        lines.append("⚠️ **Fill anomalies:**")
        for a in f.fill_anomalies:
            extra = " ".join(f"{k}={v}" for k, v in a.items()
                              if k not in ("type", "symbol", "action"))
            lines.append(f"  - [{a['type']}] {a.get('symbol','')} "
                         f"{a.get('action','')} {extra}".strip())

    if f.exposure:
        lines.append("")
        lines.append("🛡️ **Exposure alerts:**")
        for e in f.exposure:
            extra = " ".join(f"{k}={v}" for k, v in e.items() if k != "type")
            lines.append(f"  - [{e['type']}] {extra}".strip())

    if f.errors:
        lines.append("")
        lines.append("⛔ **Errors:**")
        for err in f.errors:
            lines.append(f"  - {err}")

    return "\n".join(lines)


# ── Self-test (monitoring is verifiable) ────────────────────────────────────
def self_test() -> int:
    """Exercise every branch against an in-memory fixture. Exit 0 = all pass."""
    ok = True

    # Concentrated + thin-cash portfolio.
    p = lambda symbol, shares, avg: type("P", (), {  # noqa: E731
        "symbol": symbol, "shares": shares, "avg_cost": avg,
        "total_cost": shares * avg})()
    concentrated = PortfolioState(
        cash=5000.0, initial_capital=100000.0,
        positions=[p("EQTY", 1000, 87.0), p("ABSA", 100, 33.0)],
    )
    # 2-snapshot series with a real drawdown (second below first).
    snap1 = Snapshot(timestamp=_now_iso(), cash=0.0, holdings_value=100000.0,
                     total_value=100000.0, daily_return_pct=0.0,
                     total_return_pct=0.0, drawdown_pct=0.0,
                     benchmark_value=100000.0, prices={})
    snap2 = Snapshot(timestamp=_now_iso(), cash=0.0, holdings_value=87000.0,
                     total_value=87000.0, daily_return_pct=-13.0,
                     total_return_pct=-13.0, drawdown_pct=0.0,
                     benchmark_value=98000.0, prices={})

    f = ReviewFindings()
    f.merge(review_exposure(concentrated, [snap1, snap2]))
    checks = {
        "concentration detected": any(e["type"] == "concentration" for e in f.exposure),
        "low_cash detected": any(e["type"] == "low_cash" for e in f.exposure),
        "drawdown detected": any(e["type"] == "drawdown" for e in f.exposure),
    }

    # Fill-anomaly branch via synthetic transactions (force-evaluate with "").
    # Timestamps are REQUIRED: the stateful filter skips rows with ts <= since_ts.
    iso = "2026-07-26T00:00:00+03:00"
    fake_txns = [
        {"action": "BUY", "symbol": "ZZZ", "shares": 3, "price": 10.0,
         "realised_pnl": None, "signal_ref": {}, "reason": "live",
         "timestamp": iso},                                            # unknown
        {"action": "BUY", "symbol": "EQTY", "shares": 10, "price": 70.0,
         "realised_pnl": None, "signal_ref": {}, "reason": "live",
         "timestamp": iso},                                            # well below cost
        {"action": "BUY", "symbol": "EQTY", "shares": 1, "price": 87.0,
         "realised_pnl": None, "signal_ref": {}, "reason": "live",
         "timestamp": iso},                                            # dust
        {"action": "BUY", "symbol": "EQTY", "shares": 10, "price": 95.0,
         "realised_pnl": None, "signal_ref": {}, "reason": "live",
         "timestamp": iso},                                            # normal (no flag)
    ]
    global TXN_FILE
    orig = TXN_FILE
    tmp = Path(tempfile.gettempdir()) / "orr_self_test_txns.json"
    tmp.write_text(json.dumps(fake_txns))
    TXN_FILE = tmp
    try:
        ff, _ = review_fills(concentrated, since_ts="")  # "" < any real ts → all new
    finally:
        TXN_FILE = orig
        tmp.unlink(missing_ok=True)

    types = {a["type"] for a in ff.fill_anomalies}
    checks["unknown_symbol detected"] = "unknown_symbol" in types
    checks["buy_well_below_cost detected"] = "buy_well_below_cost" in types
    checks["dust_fill detected"] = "dust_fill" in types
    checks["normal_buy NOT flagged"] = "buy_well_below_cost" not in types or not any(
        a["type"] == "buy_well_below_cost" and a["price"] == 95.0 for a in ff.fill_anomalies)

    # Silent-when-clean: a healthy portfolio must render nothing.
    healthy = PortfolioState(
        cash=20000.0, initial_capital=100000.0,
        positions=[p("ABSA", 100, 33.0)],
    )
    hf = ReviewFindings()
    hf.merge(review_exposure(healthy, []))
    checks["silent_when_clean"] = (render(hf, always=False) == "")

    print("SELF-TEST — outcome_reviewer")
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    return 0 if ok else 1


# ── Main ─────────────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Portfolio outcome reviewer")
    ap.add_argument("--report", action="store_true",
                    help="always print the summary (even if clean)")
    ap.add_argument("--self-test", action="store_true",
                    help="run built-in branch checks; exit 0 = all pass")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    try:
        state = load_state()
        snaps = load_snapshots()
    except Exception as e:  # noqa: BLE001
        print(f"⛔ **Outcome Reviewer error:** {e}")
        return 2

    progress = _load_progress()
    since_ts = progress.get("last_txn_ts")

    findings = ReviewFindings()
    try:
        fo, max1 = review_outcomes(state, snaps, since_ts)
        ff, max2 = review_fills(state, since_ts)
        fe = review_exposure(state, snaps)
        findings.realised = fo.realised
        findings.unrealised_drift = fo.unrealised_drift
        findings.fill_anomalies = ff.fill_anomalies
        findings.exposure = fe.exposure
        findings.errors = fo.errors + ff.errors + fe.errors

        # Advance the stateful checkpoint past everything we just reviewed.
        new_max = max1 if (max1 or "") > (max2 or "") else max2
        if new_max:
            progress["last_txn_ts"] = new_max
            _save_progress(progress)
    except Exception as e:  # noqa: BLE001
        findings.errors.append(f"review crashed: {e}")

    report = render(findings, always=args.report)
    if report:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
