"""Portfolio Mark-to-Market.

Reads the portfolio state, fetches live NSE prices, calculates current
value and PnL for each position, and saves an enriched snapshot.

Exports
-------
update_portfolio() -> dict
    Returns enriched portfolio with live PnL.

CLI
---
python3 -m trading.portfolio_mtm
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure trading package is importable
_TRADING_ROOT = str(Path(__file__).resolve().parent.parent)
if _TRADING_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_ROOT)

from trading.nse_price_fetcher import fetch_prices

# ── Paths ───────────────────────────────────────────────────────────────────
PORTFOLIO_DIR = Path.home() / ".trading" / "portfolio"
STATE_PATH = PORTFOLIO_DIR / "state.json"
MTM_PATH = PORTFOLIO_DIR / "mtm_state.json"


def _round2(val: Optional[float]) -> Optional[float]:
    """Round to 2 decimal places, or None if None."""
    return round(val, 2) if val is not None else None


def _load_axys_overrides() -> tuple[frozenset[str], dict[str, float]]:
    """Return (price_flagged_symbols, {symbol: axys_close}) from the most
    recent axys_closes_<date>.json (today, else up to 3 days back).

    Used so AXYS-vs-NSE official-close corrections survive the regular MTM
    refresh, which would otherwise overwrite them with pipeline feed prices.
    Flips are intentionally excluded (monitor-only). Returns empty if none.
    """
    import datetime as _dt
    try:
        for back in range(0, 4):
            d = (_dt.date.today() - _dt.timedelta(days=back)).isoformat()
            path = PORTFOLIO_DIR / f"axys_closes_{d}.json"
            if path.exists():
                data = json.loads(path.read_text())
                flags = frozenset(
                    r["symbol"] for r in data.get("rows", [])
                    if "PRICE" in (r.get("flag") or "")
                )
                close = {k: float(v) for k, v in data.get("axys", {}).items()}
                return flags, close
    except Exception:
        pass
    return frozenset(), {}


def update_portfolio() -> dict[str, Any]:
    """Read portfolio, fetch live prices, compute PnL, save mtm snapshot.

    Returns:
        Enriched portfolio dict with live PnL data.
    """
    if not STATE_PATH.exists():
        print(f"❌ Portfolio state not found: {STATE_PATH}", file=sys.stderr)
        return {"error": "state.json not found"}

    portfolio: dict[str, Any] = json.loads(STATE_PATH.read_text())
    positions = portfolio.get("positions", [])
    symbols = [p["symbol"] for p in positions]

    # AXYS reconciliation overrides (survive refresh) — see _load_axys_overrides
    _axys_flags, _axys_close = _load_axys_overrides()

    # Fetch live prices (cached 5 min internally)
    prices = fetch_prices(symbols)

    # Enrich positions with live data
    total_market_value = 0.0
    total_cost = 0.0
    enriched_positions = []

    for pos in positions:
        sym = pos["symbol"]
        shares = pos["shares"]
        cost = pos["total_cost"]
        avg_cost = pos["avg_cost"]

        price_info = prices.get(sym, {})
        live_price = price_info.get("price")
        change_pct = price_info.get("change_pct")

        current_value = round(shares * live_price, 2) if live_price else None
        pnl = round(current_value - cost, 2) if current_value else None
        pnl_pct = round(((live_price - avg_cost) / avg_cost) * 100, 2) if live_price and avg_cost else None

        # Apply AXYS price override for names flagged vs NSE official close
        if sym in _axys_flags and sym in _axys_close:
            live_price = _axys_close[sym]
            current_value = round(shares * live_price, 2) if live_price else None
            pnl = round(current_value - cost, 2) if current_value else None
            pnl_pct = round(((live_price - avg_cost) / avg_cost) * 100, 2) if live_price and avg_cost else None

        # Fallback for suspended / no-price names (e.g. BAMB delisting): carry
        # cost basis as current_value so the position is not silently dropped
        # from MTM (current_value must stay > 0 to remain reported).
        if current_value is None and avg_cost:
            live_price = avg_cost
            current_value = round(shares * avg_cost, 2)
            pnl = 0.0
            pnl_pct = 0.0

        enriched_positions.append({
            "symbol": sym,
            "shares": shares,
            "avg_cost": avg_cost,
            "total_cost": cost,
            "live_price": live_price,
            "change_pct": _round2(change_pct),
            "current_value": current_value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        })

        if current_value:
            total_market_value += current_value
        total_cost += cost

    cash = portfolio.get("cash", 0.0)
    total_portfolio_value = round(cash + total_market_value, 2)
    total_pnl = round(total_market_value - total_cost, 2)
    total_pnl_pct = round((total_pnl / total_cost) * 100, 2) if total_cost else 0.0

    mtm = {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "cash": cash,
        "initial_capital": portfolio.get("initial_capital", 0),
        "positions": enriched_positions,
        "summary": {
            "total_cost_basis": _round2(total_cost),
            "total_market_value": _round2(total_market_value),
            "total_portfolio_value": total_portfolio_value,
            "total_pnl": _round2(total_pnl),
            "total_pnl_pct": total_pnl_pct,
            "num_positions": len(enriched_positions),
        },
    }

    # Save to mtm_state.json (never overwrite state.json)
    PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
    MTM_PATH.write_text(json.dumps(mtm, indent=2))

    return mtm


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    mtm = update_portfolio()
    print(json.dumps(mtm, indent=2))

    if "error" in mtm:
        sys.exit(1)


if __name__ == "__main__":
    main()
