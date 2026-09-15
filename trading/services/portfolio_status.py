"""Canonical CURRENT portfolio status — read-only.

WHY THIS EXISTS
===============
``trading dashboard`` used to render the retired paper-engine dashboard and
print an "Archived" banner over numbers pulled from a system that was
disconnected from the auto-trader. The platform review called this out: the
public surface must not present archived data as current.

This module is the ONE read model for the current book, and it only ever
READS. Source of truth is ``portfolio/mtm_state.json`` — the auto-trader's
mark-to-market stamp (live prices). ``portfolio/state.json`` is a
cost-basis fallback used ONLY when the MTM stamp is missing; it is never
written.

No writes, no caches, no side effects: opening this module cannot damage
runtime state.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..price_source import resolve_prices

MTM_FILENAME = "mtm_state.json"
STATE_FILENAME = "state.json"

SOURCE_MTM = "mtm_state.json (mark-to-market)"
SOURCE_STATE = "state.json (cost-basis fallback)"
SOURCE_NONE = "no portfolio state found"


def portfolio_dir(dir_path: Optional[str] = None) -> Path:
    """Resolve the portfolio dir at call time so HOME overrides work."""
    if dir_path:
        return Path(dir_path)
    return Path(os.path.expanduser("~/.trading/portfolio"))


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _age_days(value: Any) -> Optional[float]:
    ts = _parse_timestamp(value)
    if ts is None:
        return None
    delta = datetime.now(timezone.utc) - ts.astimezone(timezone.utc)
    return round(delta.total_seconds() / 86400.0, 2)


def current_status(
    dir_path: Optional[str] = None,
    as_of: Any = None,
) -> dict[str, Any]:
    """Return the canonical current portfolio status.

    Shape (stable, JSON-serialisable)::

        {
          "mode": "PAPER",
          "as_of": "<mtm generated_at or unknown>",
          "as_of_date": "2026-09-15",
          "age_days": 0.84,
          "source": "mtm_state.json (mark-to-market)",
          "valuation_basis": "...",
          "portfolio_value": 105275.28,
          "cash": 23020.0,
          "invested": 82255.28,
          "initial_capital": 100000.0,
          "positions": [ {symbol, shares, live_price, current_value, pnl, pnl_pct,
                          price_source}, ... ],
          "position_count": 9,
          "total_pnl": 0.56,
          "total_pnl_pct": 0.0,
          "eligible_universe": ["ABSA", ...],
          "resolver": {as_of, price_date, official_close_file,
                       official_close_age_days, stale, sources},
          "current": True,
        }

    ``price_source`` per position comes from the shared authority-chain
    resolver (:mod:`trading.price_source`: AXYS official close > feed > CSV),
    so every surface can show WHERE a mark came from. The valuation itself
    stays the auto-trader's mark-to-market stamp — this function never
    rewrites it, it only labels it.

    Missing state is reported explicitly rather than as a zero book.
    """
    base = portfolio_dir(dir_path)
    mtm_path = base / MTM_FILENAME
    state_path = base / STATE_FILENAME

    if mtm_path.exists():
        try:
            with open(mtm_path) as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            return _with_provenance(_empty(f"unreadable {MTM_FILENAME}: {exc}"), base, as_of)
        status = _from_mtm(data)
        status["valuation_basis"] = "auto-trader mark-to-market stamp (mtm_state.json)"
        return _with_provenance(status, base, as_of)

    if state_path.exists():
        try:
            with open(state_path) as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            return _with_provenance(_empty(f"unreadable {STATE_FILENAME}: {exc}"), base, as_of)
        status = _from_state(data)
        status["valuation_basis"] = "cost basis (state.json fallback, MTM stamp missing)"
        return _with_provenance(status, base, as_of)

    return _with_provenance(_empty(SOURCE_NONE), base, as_of)


def _with_provenance(
    status: dict[str, Any], base: Path, as_of: Any = None
) -> dict[str, Any]:
    """Attach the shared resolver's provenance to a status payload."""
    symbols = [
        p.get("symbol") for p in status.get("positions") or [] if p.get("symbol")
    ]
    status["eligible_universe"] = symbols
    status["as_of_date"] = (status.get("as_of") or "")[:10] or None
    if status.get("portfolio_value") is None:
        status["resolver"] = None
        status["price_date"] = None
        return status
    res = resolve_prices(symbols, str(base), as_of=as_of)
    for pos in status.get("positions") or []:
        pos["price_source"] = res.sources.get(pos.get("symbol"), "stored")
    status["resolver"] = res.provenance()
    status["price_date"] = res.price_date
    return status


def _empty(source: str) -> dict[str, Any]:
    return {
        "mode": "PAPER",
        "as_of": None,
        "as_of_date": None,
        "age_days": None,
        "source": source,
        "valuation_basis": "no state",
        "portfolio_value": None,
        "cash": None,
        "invested": None,
        "initial_capital": None,
        "positions": [],
        "position_count": 0,
        "total_pnl": None,
        "total_pnl_pct": None,
        "eligible_universe": [],
        "resolver": None,
        "price_date": None,
        "current": False,
    }


def _from_mtm(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary") or {}
    cash = float(data.get("cash") or 0.0)
    positions = list(data.get("positions") or [])
    invested = summary.get("total_market_value")
    if invested is None:
        invested = sum(float(p.get("current_value") or 0.0) for p in positions)
    value = summary.get("total_portfolio_value")
    if value is None:
        value = cash + float(invested)
    as_of = data.get("generated_at")
    return {
        "mode": "PAPER",
        "as_of": as_of,
        "age_days": _age_days(as_of),
        "source": SOURCE_MTM,
        "portfolio_value": round(float(value), 2),
        "cash": round(cash, 2),
        "invested": round(float(invested), 2),
        "initial_capital": data.get("initial_capital"),
        "positions": [
            {
                "symbol": p.get("symbol"),
                "shares": p.get("shares"),
                "avg_cost": p.get("avg_cost"),
                "total_cost": p.get("total_cost"),
                "live_price": p.get("live_price"),
                "current_value": p.get("current_value"),
                "pnl": p.get("pnl"),
                "pnl_pct": p.get("pnl_pct"),
            }
            for p in positions
        ],
        "position_count": len(positions),
        "total_pnl": summary.get("total_pnl"),
        "total_pnl_pct": summary.get("total_pnl_pct"),
        "current": True,
    }


def _from_state(data: dict[str, Any]) -> dict[str, Any]:
    positions = list(data.get("positions") or [])
    cash = float(data.get("cash") or 0.0)
    invested = sum(
        float(p.get("shares") or 0) * float(p.get("avg_cost") or 0.0)
        for p in positions
    )
    as_of = data.get("updated_at")
    return {
        "mode": "PAPER",
        "as_of": as_of,
        "age_days": _age_days(as_of),
        "source": SOURCE_STATE,
        "portfolio_value": round(cash + invested, 2),
        "cash": round(cash, 2),
        "invested": round(invested, 2),
        "initial_capital": data.get("initial_capital"),
        "positions": [
            {
                "symbol": p.get("symbol"),
                "shares": p.get("shares"),
                "avg_cost": p.get("avg_cost"),
                "total_cost": round(
                    float(p.get("shares") or 0) * float(p.get("avg_cost") or 0.0), 2
                ),
                "live_price": p.get("avg_cost"),
                "current_value": round(
                    float(p.get("shares") or 0) * float(p.get("avg_cost") or 0.0), 2
                ),
                "pnl": None,
                "pnl_pct": None,
            }
            for p in positions
        ],
        "position_count": len(positions),
        "total_pnl": None,
        "total_pnl_pct": None,
        "current": True,
    }


def format_status(status: dict[str, Any]) -> str:
    """Render the canonical status as plain text (no Rich markup)."""
    lines: list[str] = []
    lines.append("Portfolio — current status (read-only)")
    lines.append("=" * 52)
    lines.append(f"  Mode:     {status.get('mode', 'PAPER')} (no real broker)")
    lines.append(f"  Source:   {status.get('source')}")
    if status.get("valuation_basis"):
        lines.append(f"  Basis:    {status['valuation_basis']}")
    as_of = status.get("as_of") or "unknown"
    age = status.get("age_days")
    age_note = f"  ({age:g} days old)" if isinstance(age, (int, float)) else ""
    lines.append(f"  As of:    {as_of}{age_note}")
    value = status.get("portfolio_value")
    if value is None:
        lines.append("  No current portfolio state available.")
        return "\n".join(lines) + "\n"
    lines.append(f"  Value:    KES {value:,.2f}")
    lines.append(f"  Cash:     KES {status.get('cash') or 0:,.2f}")
    lines.append(f"  Invested: KES {status.get('invested') or 0:,.2f}")
    lines.append(f"  Positions:{status.get('position_count', 0):>3d}")
    if status.get("total_pnl") is not None:
        lines.append(
            f"  P&L:      KES {status['total_pnl']:,.2f} "
            f"({status.get('total_pnl_pct') or 0:+.2f}%)"
        )
    lines.append("")
    lines.append(f"  {'SYMBOL':<8} {'SHARES':>8} {'PRICE':>9} {'VALUE':>12} {'P&L%':>8}")
    lines.append("  " + "-" * 48)
    for pos in status.get("positions", []):
        price = pos.get("live_price")
        value_ = pos.get("current_value")
        pnl_pct = pos.get("pnl_pct")
        lines.append(
            f"  {str(pos.get('symbol')):<8} {pos.get('shares') or 0:>8} "
            f"{(f'{price:,.2f}' if isinstance(price, (int, float)) else '—'):>9} "
            f"{(f'{value_:,.2f}' if isinstance(value_, (int, float)) else '—'):>12} "
            f"{(f'{pnl_pct:+.2f}' if isinstance(pnl_pct, (int, float)) else '—'):>8}"
        )
    return "\n".join(lines) + "\n"


def format_status_html(status: dict[str, Any]) -> str:
    """Minimal HTML rendering of the same canonical payload."""
    rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            p.get("symbol") or "",
            p.get("shares") or 0,
            p.get("live_price") if p.get("live_price") is not None else "—",
            p.get("current_value") if p.get("current_value") is not None else "—",
            p.get("pnl_pct") if p.get("pnl_pct") is not None else "—",
        )
        for p in status.get("positions", [])
    )
    value = status.get("portfolio_value")
    value_txt = f"KES {value:,.2f}" if isinstance(value, (int, float)) else "unavailable"
    return (
        "<html><head><title>Portfolio — current status</title></head><body>"
        "<h1>Portfolio — current status</h1>"
        "<p><strong>PAPER MODE</strong> — no real broker integration.</p>"
        f"<p>Source: {status.get('source')}<br>As of: {status.get('as_of')}</p>"
        f"<p>Portfolio value: <strong>{value_txt}</strong></p>"
        "<table border='1' cellpadding='4'>"
        "<tr><th>Symbol</th><th>Shares</th><th>Price</th><th>Value</th><th>P&amp;L %</th></tr>"
        f"{rows}</table></body></html>"
    )
