"""``trading gap-scan`` — NSE pre-market gap scanner.

Uses Mansa API to find stocks that gapped significantly from
yesterday's close. Designed to run at ~09:00 EAT.
NOTE: Mansa free-tier prices may lag; absolute prices are from Mansa
but the % change (gap) is what matters for this scan.

Exports
-------
run(threshold=2.0, quiet=False, as_json=False, cron=False) -> int
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────
MANSA_BASE = "https://mansaapi.com/api/v1"
MANSA_KEY = os.environ.get("MANSA_API_KEY", "")

# Cross-verify threshold: if Mansa and mystocks gaps differ by more than
# this %, flag as unverified.
VERIFICATION_TOLERANCE_PCT = 3.0

# We only care about our tracked stocks + a few extras
WATCH_SYMBOLS = {
    "SCOM", "KCB", "EQTY", "EABL", "ABSA", "SCBK",
    "COOP", "KPLC", "TOTL", "KNRE", "WTK",
}


# ── Core ──────────────────────────────────────────────────────────────────
def _fetch_movers() -> dict[str, Any]:
    """Hit Mansa movers endpoint. Returns raw JSON or raises."""
    if not MANSA_KEY:
        return {"success": False, "reason": "MANSA_API_KEY not set"}

    url = f"{MANSA_BASE}/markets/exchanges/KENYA/movers"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {MANSA_KEY}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _mystocks_verify(symbol: str) -> dict[str, float] | None:
    """Cross-verify a gap by checking mystocks current price against CSV prev close.

    Returns: {"price": float, "prev_close": float, "gap_pct": float} or None.
    """
    import csv
    from pathlib import Path

    try:
        # 1. Get current price from mystocks scraper cache
        cache_path = Path.home() / ".trading" / "cache" / f"live-prices-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        mystocks_price = None
        if cache_path.exists():
            with open(cache_path) as f:
                data = json.load(f)
            mystocks_price = data.get("stocks", {}).get(symbol)

        # 2. Get previous close from NSE CSV
        csv_path = Path.home() / ".trading" / "data" / f"nse_{symbol}.csv"
        prev_close = None
        if csv_path.exists():
            with open(csv_path) as f:
                rows = list(csv.DictReader(f))
            if rows:
                _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                _last_date = rows[-1].get("date", "").strip()
                if _last_date == _today and len(rows) >= 2:
                    prev_close = float(rows[-2].get("close", rows[-2].get("Close", 0)))
                elif _last_date == _today:
                    prev_close = 0.0  # No prior-day data → _prev > 0 guard skips
                else:
                    prev_close = float(rows[-1].get("close", rows[-1].get("Close", 0)))

        if mystocks_price and prev_close and prev_close > 0:
            gap_pct = ((mystocks_price - prev_close) / prev_close) * 100
            return {"price": mystocks_price, "prev_close": prev_close, "gap_pct": round(gap_pct, 2)}

    except Exception:
        pass

    return None


def run(
    threshold: float = 2.0,
    all_stocks: bool = False,
    quiet: bool = False,
    as_json: bool = False,
    cron: bool = False,
) -> int:
    """Scan for NSE price gaps above *threshold* percent.

    Args:
        threshold: Minimum absolute % change to flag (default 2.0).
        all_stocks: Show ALL movers, not just watched symbols.
        quiet: Minimal output.
        as_json: Output JSON.
        cron: When True, silently skip if outside trading hours (08:30-14:00 EAT).

    Returns:
        0 on success, 1 if no data, 2 on error.
    """
    # ── Time guard for cron mode ──────────────────────────────────────
    if cron:
        now_utc = datetime.now(timezone.utc)
        # EAT = UTC+3
        eat_hour = now_utc.hour + 3
        eat_min = now_utc.minute
        # Normalise to 0-24 range
        if eat_hour >= 24:
            eat_hour -= 24
        eat_total_min = eat_hour * 60 + eat_min
        # Only run between 08:30 and 14:00 EAT on weekdays
        if now_utc.weekday() >= 5:  # Saturday=5, Sunday=6
            return 0  # silently skip weekends
        if eat_total_min < 8 * 60 + 30 or eat_total_min > 14 * 60:
            return 0  # silently skip outside trading hours
    try:
        raw = _fetch_movers()
    except urllib.error.HTTPError as exc:
        msg = f"Mansa API error: {exc.code} {exc.reason}"
        print(msg, file=sys.stderr)
        return 2
    except urllib.error.URLError as exc:
        msg = f"Network error: {exc.reason}"
        print(msg, file=sys.stderr)
        return 2

    if not raw.get("success"):
        reason = raw.get("reason", raw.get("error", "Unknown"))
        msg = f"Mansa API failure: {reason}"
        print(msg, file=sys.stderr)
        return 2

    data = raw.get("data", {})
    gainers: list[dict] = data.get("gainers", [])
    losers: list[dict] = data.get("losers", [])

    # Combine, deduplicate by ticker, and filter by threshold
    seen: set[str] = set()
    all_movers: list[dict] = []
    for stock in gainers + losers:
        ticker = stock.get("ticker", "")
        if ticker in seen:
            continue
        seen.add(ticker)
        chg_pct = stock.get("change_pct", 0) or 0
        if abs(chg_pct) >= threshold and (all_stocks or ticker in WATCH_SYMBOLS):
            all_movers.append(stock)

    # Sort by absolute gap (largest first)
    all_movers.sort(key=lambda s: abs(s.get("change_pct", 0) or 0), reverse=True)

    if not all_movers:
        if quiet:
            return 0
        print("📍 No significant gaps found above {:.1f}% threshold.".format(threshold))
        return 0

    if as_json:
        print(json.dumps({"gap_scan": all_movers, "threshold": threshold}, indent=2))
        return 0

    # Human output
    print(f"📍 NSE Gap Scan — {len(all_movers)} stocks gapped >{threshold:.1f}%")

    # ── Cross-verify with mystocks ──────────────────────────────────
    verified_gaps: list[dict] = []
    unverified_gaps: list[dict] = []
    for s in all_movers:
        if s["ticker"] not in WATCH_SYMBOLS:
            unverified_gaps.append(s)  # don't verify non-watched stocks
            continue
        mv = _mystocks_verify(s["ticker"])
        if mv and abs(mv["gap_pct"] - s["change_pct"]) <= VERIFICATION_TOLERANCE_PCT:
            s["verified"] = True
            s["mystocks_price"] = mv["price"]
            verified_gaps.append(s)
        elif mv:
            s["verified"] = False
            s["mystocks_price"] = mv["price"]
            s["mystocks_gap"] = mv["gap_pct"]
            unverified_gaps.append(s)
        else:
            s["verified"] = None
            unverified_gaps.append(s)
    print()

    # Track watched vs others
    # verified_gaps already filtered for watched symbols above
    others = [s for s in all_movers if s["ticker"] not in WATCH_SYMBOLS]

    def _print_group(stocks: list[dict], label: str) -> None:
        if not stocks:
            return
        print(f"  ── {label} ──")
        for s in stocks:
            chg = s["change_pct"]
            arrow = "▲" if chg > 0 else "▼"
            line = (
                f"  {s['ticker']:<8s}  {arrow} {chg:+.2f}%    "
                f"KES {float(s['price']):<9.2f}  vol {s.get('volume', 0):,}"
            )
            # Cross-verification tag
            if s.get("verified") is True:
                line += f"  ✅ mystocks {float(s.get('mystocks_price',0)):.2f}"
            elif s.get("verified") is False:
                mgap = s.get("mystocks_gap", 0)
                mprice = s.get("mystocks_price", 0)
                line += f"  ⚠️ mystocks: {mgap:+.2f}% (KES {mprice:.2f})"
            print(line)
        print()

    _print_group(verified_gaps, "Your Watchlist — ✅ Verified")
    _print_group(unverified_gaps, "⚠️ Unverified / Needs Attention")
    _print_group(others, "All NSE")

    if not quiet:
        source_note = data.get("data_freshness", "30min")
        print(f"  Source: Mansa ← cross-verified with mystocks (tolerance ±{VERIFICATION_TOLERANCE_PCT}%)")

    return 0
