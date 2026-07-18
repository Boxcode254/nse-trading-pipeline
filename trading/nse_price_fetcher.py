"""NSE Live Price Fetcher.

Fetches current NSE stock prices using the existing market service
(which routes to TradingView via tradingview_ta for NSE stocks).

Caches results for 5 minutes to avoid excessive API calls.

Exports
-------
fetch_prices(symbols=None) -> dict[str, dict]
    Returns {symbol: {price, previous_close, change_abs, change_pct, source, cached_at}}

CLI
---
python3 -m trading.nse_price_fetcher [SYMBOL ...]
"""

from __future__ import annotations

import sys
import json
import time
from typing import Optional
from pathlib import Path

# Ensure trading package is importable
_TRADING_ROOT = str(Path(__file__).resolve().parent.parent)
if _TRADING_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_ROOT)

from trading.services.market import latest_price
from trading import config

# ── Cache ──────────────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}
_cache_ts: float = 0.0
CACHE_TTL = 300  # 5 minutes


def _is_cache_fresh() -> bool:
    return bool(_cache) and (time.time() - _cache_ts) < CACHE_TTL


def fetch_prices(symbols: Optional[list[str]] = None) -> dict[str, dict]:
    """Fetch current prices for NSE stock symbols.

    Args:
        symbols: List of symbols (e.g. ["SCOM", "COOP", "ABSA"]).
                 Defaults to equity symbols from trading.config.

    Returns:
        {symbol: {price, previous_close, change_abs, change_pct, source, cached_at}}
    """
    global _cache, _cache_ts

    if _is_cache_fresh():
        return dict(_cache)

    if symbols is None:
        symbols = config.get_equity_symbols()

    results: dict[str, dict] = {}
    errors: list[str] = []

    for symbol in symbols:
        try:
            info = latest_price(symbol)
            price = info.get("price")
            if price is None:
                errors.append(f"{symbol}: no price data")
                continue

            results[symbol] = {
                "symbol": symbol,
                "price": round(float(price), 4),
                "previous_close": round(float(info.get("previous_close", price)), 4),
                "change_abs": round(float(info.get("change_abs", 0)), 4),
                "change_pct": round(float(info.get("change_pct", 0)), 2),
                "date": info.get("date", ""),
                "source": info.get("source", "tradingview"),
                "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        except Exception as e:
            errors.append(f"{symbol}: {e}")

    _cache = results
    _cache_ts = time.time()

    if errors:
        results["_errors"] = errors

    return results


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    symbols = sys.argv[1:] if len(sys.argv) > 1 else None
    prices = fetch_prices(symbols)

    # Print symbol → price dict
    output = {}
    for key, val in prices.items():
        if key == "_errors":
            continue
        p = val["price"]
        chg = val["change_pct"]
        arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "—")
        output[key] = {
            "price": p,
            "change_pct": chg,
            "change_abs": val["change_abs"],
            "previous_close": val["previous_close"],
            "date": val.get("date", ""),
            "arrow": arrow,
        }

    print(json.dumps(output, indent=2))

    errors = prices.get("_errors", [])
    if errors:
        print("\n⚠️  Errors:", file=sys.stderr)
        for e in errors:
            print(f"   {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
