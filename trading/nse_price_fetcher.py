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
import socket
import threading
from typing import Optional
from pathlib import Path

# Ensure trading package is importable
_TRADING_ROOT = str(Path(__file__).resolve().parent.parent)
if _TRADING_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_ROOT)

from trading.services.market import latest_price
from trading.execution.retry import call_with_timeout
from trading import config

# ── Cache ──────────────────────────────────────────────────────────────────
# In-memory cache (per-process) + on-disk mirror so the cron process reuses
# results instead of cold-fetching every run.
_cache: dict[str, dict] = {}
_cache_ts: float = 0.0
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 minutes (max age before we force a refetch)

# On-disk cache path (mirror of the in-memory cache so separate processes share it)
_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "macro_price_cache.json"

# Per-symbol network bound: no single TradingView fetch may exceed this.
_PER_SYMBOL_TIMEOUT = 5.0
# Global cap on the whole fetch so a slow upstream can never stall the run > ~15s.
_FETCH_TIMEOUT = 15.0

# Process-wide socket timeout safety net (defence-in-depth under call_with_timeout).
socket.setdefaulttimeout(_PER_SYMBOL_TIMEOUT + 2.0)


def _load_disk_cache() -> None:
    """Load the on-disk cache mirror into the in-memory cache if fresh."""
    global _cache, _cache_ts
    if _CACHE_FILE.exists():
        try:
            with open(_CACHE_FILE) as f:
                blob = json.load(f)
            ts = blob.get("_cache_ts", 0.0)
            # Only adopt the disk cache if it is still within TTL; otherwise
            # treat it as stale and force a refetch (never trade yesterday's data).
            if (time.time() - ts) < CACHE_TTL and blob.get("prices"):
                _cache = blob["prices"]
                _cache_ts = ts
        except (OSError, json.JSONDecodeError, ValueError):
            pass


def _save_disk_cache() -> None:
    """Persist the in-memory cache to disk so other processes can reuse it."""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"_cache_ts": _cache_ts, "prices": _cache}, f)
    except OSError:
        pass


def _cache_is_fresh() -> bool:
    return bool(_cache) and (time.time() - _cache_ts) < CACHE_TTL


def _is_cache_fresh() -> bool:
    return _cache_is_fresh()


def _fetch_one_bounded(symbol: str) -> Optional[dict]:
    """Fetch a single symbol's price, bounded by _PER_SYMBOL_TIMEOUT.

    Returns the price dict, or None if the call times out / raises. The
    timeout is enforced via call_with_timeout (thread join) AND a process-wide
    socket.setdefaulttimeout safety net, so a slow TradingView upstream can
    never stall the run longer than ~_PER_SYMBOL_TIMEOUT per symbol.
    """
    completed, result, err = call_with_timeout(
        lambda: latest_price(symbol), _PER_SYMBOL_TIMEOUT
    )
    if not completed or result is None:
        return None
    return result


def fetch_prices(symbols: Optional[list[str]] = None) -> dict[str, dict]:
    """Fetch current prices for NSE stock symbols.

    Each symbol is fetched through a per-symbol timeout (_PER_SYMBOL_TIMEOUT)
    and the whole scan is additionally capped by _FETCH_TIMEOUT via the caller
    (safety.refresh_macro). Results are cached in-memory AND on disk so a
    separate cron process reuses them instead of cold-fetching; the cache is
    rejected once it is older than CACHE_TTL (max-age invalidation) so a stale
    snapshot can never silently feed a gap-filter / macro decision.

    Args:
        symbols: List of symbols (e.g. ["SCOM", "COOP", "ABSA"]).
                 Defaults to equity symbols from trading.config.

    Returns:
        {symbol: {price, previous_close, change_abs, change_pct, source, cached_at}}
    """
    global _cache, _cache_ts

    # Adopt the on-disk mirror once at process start if it is still fresh.
    if not _cache:
        _load_disk_cache()

    if _cache_is_fresh():
        return dict(_cache)

    if symbols is None:
        symbols = config.get_equity_symbols()

    results: dict[str, dict] = {}
    errors: list[str] = []

    # Global deadline: the whole scan self-bounds at _FETCH_TIMEOUT even if
    # many symbols time out in sequence (per-symbol timeouts would otherwise
    # accumulate: N_symbols * _PER_SYMBOL_TIMEOUT). Stops the loop early once
    # the budget is spent, so a slow upstream can never stall the caller > ~15s.
    deadline = time.time() + _FETCH_TIMEOUT

    for symbol in symbols:
        if time.time() >= deadline:
            errors.append(f"{symbol}: skipped — global fetch budget exhausted")
            continue
        try:
            info = _fetch_one_bounded(symbol)
            if info is None:
                errors.append(f"{symbol}: fetch timed out / no data")
                continue
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

    with _cache_lock:
        _cache = results
        _cache_ts = time.time()
        _save_disk_cache()

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
