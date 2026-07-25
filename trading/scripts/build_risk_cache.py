"""Best-effort local price-history cache for volatility/correlation profiling.

Fetches ~1y of daily OHLCV from the backtest history fetcher for each NSE
equity symbol and stores ``{close:[...], volume:[...]}`` to
``~/.trading/portfolio/price_history/<SYMBOL>.json``.

This cache is OPTIONAL. ``trading.risk_profiles`` / ``target_allocation``
degrade gracefully to ranking factor scores when the cache (or individual
symbols) are missing. Building it only *upgrades* realized vol/corr when
present; it never blocks or changes the live rebalance path.

Fail-open: any per-symbol fetch error is logged and skipped. A ``--dry-run``
prints what would be fetched without writing anything.

Usage:
    python3 -m trading.scripts.build_risk_cache [--dry-run] [--years 1.0]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root importable
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from trading import config
from trading.backtest import fetch_history


def _cache_dir() -> Path:
    d = Path.home() / ".trading" / "portfolio" / "price_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_symbol(symbol: str, years: float, cache_dir: Path, dry_run: bool) -> bool:
    """Fetch + cache one symbol. Returns True on success."""
    try:
        df = fetch_history(symbol, years=years)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️  {symbol}: fetch failed ({exc})", file=sys.stderr)
        return False
    if df is None or df.empty:
        print(f"  ⚠️  {symbol}: empty history", file=sys.stderr)
        return False
    closes = [round(float(x), 4) for x in df["close"].tolist()]
    volumes = [round(float(x), 2) for x in df.get("volume", [0] * len(closes)).tolist()]
    payload = {"symbol": symbol, "close": closes, "volume": volumes}
    if dry_run:
        print(f"  ✅ {symbol}: {len(closes)} bars (dry-run, not written)")
        return True
    out = cache_dir / f"{symbol}.json"
    out.write_text(json.dumps(payload))
    print(f"  ✅ {symbol}: {len(closes)} bars -> {out.name}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Print plan, write nothing")
    ap.add_argument("--years", type=float, default=1.0, help="History window in years")
    args = ap.parse_args()

    symbols = config.get_equity_symbols()
    cache_dir = _cache_dir()
    print(f"{'[DRY-RUN] ' if args.dry_run else ''}Building risk cache for "
          f"{len(symbols)} symbols ({args.years}y) -> {cache_dir}")
    ok = 0
    for sym in symbols:
        if build_symbol(sym, args.years, cache_dir, args.dry_run):
            ok += 1
    print(f"Done: {ok}/{len(symbols)} symbols cached "
          f"({'dry-run' if args.dry_run else 'written'}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
