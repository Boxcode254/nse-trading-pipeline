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
import os
import sys
import urllib.request
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

# Stale-source guard constants.
#   AXYS_SEARCH_WINDOW_DAYS : how far back the loader will look for an
#       axys_closes file to apply. 7 days is a legitimate technical safety net —
#       it means a missed-day gap still gets the most recent official close
#       rather than degrading all the way to feed. (UNCHANGED behaviour.)
#   STALE_WARN_MAX_DAYS : freshness STANDARD for alerting. If the newest
#       official-close file is older than this, WARN + alert — at 3 days,
#       matching axys_reconcile.py / book_integrity_check.py. This decouples
#       the alert from the 7-day search window so we are NOT silent for a full
#       week before telling anyone (the exact failure class this effort exists
#       to kill). Per Kratos override (2026-08-12): keep 7-day search, fire WARN
#       at 3 days idle.
AXYS_SEARCH_WINDOW_DAYS = 7
STALE_WARN_MAX_DAYS = 3  # matches the other two guards' freshness standard


def _axys_file_date(filename: str):
    """Extract ISO date from 'axys_closes_YYYY-MM-DD.json' or None."""
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename or "")
    return m.group(1) if m else None


def _send_stale_axys_alert(reason: str) -> None:
    """WARN + notify when the AXYS lookback comes up empty.

    Mirrors the alert channel used by book_integrity_check.py / axys_reconcile.py
    (Telegram via TELEGRAM_BOT_TOKEN -> CRON_ALERT_CHAT_ID). We do NOT invent a
    new channel. Best-effort: network/cred failures are swallowed (logged to
    stderr) so the MTM refresh still completes — the stale condition is also
    recorded on stdout/return for the caller and the cron log.
    """
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
        chat = os.environ.get("CRON_ALERT_CHAT_ID") or ""
        # Fallback to the .hermes/.env values if env vars are empty
        if not token or not chat:
            env_path = Path.home() / ".hermes" / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("TELEGRAM_BOT_TOKEN=") and not token:
                        token = line.split("=", 1)[1].strip().strip('"')
                    elif line.startswith("CRON_ALERT_CHAT_ID=") and not chat:
                        chat = line.split("=", 1)[1].strip().strip('"')
        if not token or not chat:
            print("[portfolio_mtm] stale-AXYS WARN: no Telegram creds; "
                  "logging only", file=sys.stderr)
            return
        payload = json.dumps({
            "chat_id": chat,
            "text": "⚠️ Stale AXYS source (portfolio_mtm)\n\n" + reason,
            "disable_notification": False,
        }).encode()
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + token + "/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            if b'"ok":true' not in r.read():
                print("[portfolio_mtm] Telegram returned non-ok for stale alert",
                      file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[portfolio_mtm] stale-AXYS alert send failed: {e}", file=sys.stderr)


def _round2(val: Optional[float]) -> Optional[float]:
    """Round to 2 decimal places, or None if None."""
    return round(val, 2) if val is not None else None


def _load_axys_overrides(alert: bool = True) -> tuple[frozenset[str], dict[str, float], dict[str, float]]:
    """Return (price_flagged_symbols, {symbol: axys_close_today}, {symbol: axys_close_prev}).

    Reads the most recent axys_closes_<date>.json (today, else up to 6 days
    back) for the price-flagged set and today's official closes, plus the
    next-oldest axys_closes file for prior-day closes — used so the day-change
    can be recomputed authoritatively from the NSE tape (close-to-close) when
    an AXYS override is applied. This keeps the position's price AND its
    direction consistent with the official tape, avoiding false direction-flips.

    Flips are intentionally excluded from `flags` (monitor-only). Returns empty
    if no axys file is found.
    """
    import datetime as _dt
    try:
        files = []
        # range(0, WINDOW+1) is INCLUSIVE of WINDOW days back: a file exactly
        # AXYS_SEARCH_WINDOW_DAYS old is still applied (with a stale WARN, since
        # it's > STALE_WARN_MAX_DAYS). The constant name promises "7 days"; an
        # exclusive range(0,7) would silently drop the 7th day (off-by-one).
        for back in range(0, AXYS_SEARCH_WINDOW_DAYS + 1):
            d = (_dt.date.today() - _dt.timedelta(days=back)).isoformat()
            path = PORTFOLIO_DIR / f"axys_closes_{d}.json"
            if path.exists():
                files.append(path)
        if not files:
            # No correction file anywhere in the search window: MTM reverts to
            # pure feed with NO official-close correction. Previously silent; now
            # WARN + alert so the gap is visible (like the other two guards).
            msg = (
                f"No AXYS official-close file found in the trailing "
                f"{AXYS_SEARCH_WINDOW_DAYS} days. MTM is falling back to feed "
                f"prices with NO official-close correction. Forward today's "
                f"Daily_Market_Watch PDF to refresh, or this is an unreconciled gap."
            )
            print(f"[portfolio_mtm] WARNING: {msg}", file=sys.stderr)
            if alert:
                _send_stale_axys_alert(msg)
            return frozenset(), {}, {}
        # Stale-but-present: newest file older than the 3-day freshness standard.
        # We STILL apply it (within the 7-day search safety net, better than
        # feed) but WARN + alert so nobody mistakes it for trusted forward data.
        # Trips at 3 days idle, matching axys_reconcile.py / book_integrity_check.py;
        # does NOT wait a full week of silent feed-only degradation.
        _newest_iso = _axys_file_date(files[0].name)
        if _newest_iso:
            _age = (_dt.date.today() - _dt.date.fromisoformat(_newest_iso)).days
            if _age > STALE_WARN_MAX_DAYS:
                _msg = (
                    f"Newest AXYS official-close file is {_age} days old "
                    f"({files[0].name}); older than the {STALE_WARN_MAX_DAYS}-day "
                    f"freshness standard. MTM is still applying it as the best "
                    f"available official correction, but forward data (from "
                    f"2026-07-23) is the only fully-trusted record. Forward "
                    f"today's Daily_Market_Watch PDF to refresh."
                )
                print(f"[portfolio_mtm] WARNING: {_msg}", file=sys.stderr)
                if alert:
                    _send_stale_axys_alert(_msg)
        today_data = json.loads(files[0].read_text())
        flags = frozenset(
            r["symbol"] for r in today_data.get("rows", [])
            if "PRICE" in (r.get("flag") or "")
        )
        close_today = {k: float(v) for k, v in today_data.get("axys", {}).items()}
        close_prev: dict[str, float] = {}
        if len(files) > 1:
            prev_data = json.loads(files[1].read_text())
            close_prev = {k: float(v) for k, v in prev_data.get("axys", {}).items()}
        return flags, close_today, close_prev
    except Exception:
        return frozenset(), {}, {}


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
    _axys_flags, _axys_close, _axys_prev = _load_axys_overrides()

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

        # Price authoritative from AXYS NSE official close for every covered
        # name (not just price-flagged ones). AXYS is the NSE tape; the live
        # feed routinely drifts 0.8-1.3% from it intraday/after-hours. We keep
        # the feed price only for names AXYS does not cover at all.
        if sym in _axys_close and _axys_close[sym]:
            live_price = _axys_close[sym]
        # Day-change authoritative from AXYS close-to-close whenever we have
        # both today's and prior day's official closes. The live feed's
        # change_pct is unreliable (sign errors observed on KCB, EQTY). AXYS
        # is the NSE official tape, so it wins for both price and direction.
        if sym in _axys_close and sym in _axys_prev and _axys_prev[sym]:
            change_pct = round(
                ((_axys_close[sym] - _axys_prev[sym]) / _axys_prev[sym]) * 100, 2
            )
        if live_price is not None:
            current_value = round(shares * live_price, 2)
            pnl = round(current_value - cost, 2)
            pnl_pct = round(((live_price - avg_cost) / avg_cost) * 100, 2) if avg_cost else None

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
