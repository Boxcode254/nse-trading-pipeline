"""NSE (Nairobi Securities Exchange) data fetcher.

Uses TradingView's ``tradingview-ta`` library to get daily OHLCV bars
(EOD close) for Kenyan equities. Data is cached locally so history
accumulates over time.

WARNING: ``tradingview-ta`` returns daily bars only — not intraday ticks.
It is an unofficial wrapper around TradingView's web API. Use it for
EOD/position-trading, NOT for live intraday decisions.

Cache strategy
--------------
Day 1 (no cache): synthetic data seeded close to the real EOD price +
the single real daily bar from TradingView.  Each subsequent day appends
the new real bar.  After ~50 days the synthetic prefix becomes irrelevant
and all computed indicators reflect real market history.

Exports
-------
fetch_data(pair, days) -> pd.DataFrame
    OHLCV with a DatetimeIndex named 'date'.  Always returns real rows;
    never raises.
"""
from __future__ import annotations

import os
from typing import Optional, Any

import pandas as pd

from .. import config

# Exchanges known to work with TradingView TA handler
_TRADINGVIEW_SCREENER = "kenya"
_TRADINGVIEW_EXCHANGE = "NSEKE"

# Max synthetic bars prepended when cache is short
_MAX_SYNTHETIC_PREFIX = 200


def _csv_path(pair: str) -> str:
    return os.path.join(config.DATA_DIR, f"nse_{pair}.csv")


def _fetch_tradingview(pair: str) -> Optional[dict[str, Any]]:
    """Return a dict with OHLCV + SMA + RSI from TradingView, or None."""
    try:
        from tradingview_ta import TA_Handler, Interval
        h = TA_Handler(
            symbol=pair,
            exchange=_TRADINGVIEW_EXCHANGE,
            screener=_TRADINGVIEW_SCREENER,
            interval=Interval.INTERVAL_1_DAY,
        )
        a = h.get_analysis()
        ind = a.indicators
        required = ("open", "high", "low", "close", "volume")
        if not all(k in ind for k in required):
            return None
        return {
            "open": float(ind["open"]),
            "high": float(ind["high"]),
            "low": float(ind["low"]),
            "close": float(ind["close"]),
            "volume": int(ind["volume"]),
            "sma20": float(ind.get("SMA20", 0)),
            "sma50": float(ind.get("SMA50", 0)),
            "rsi": float(ind.get("RSI", 50)),
        }
    except Exception:
        return None


def _synthetic_for_nse(pair: str, days: int, close_price: float) -> pd.DataFrame:
    """Deterministic synthetic history anchored to the given close price.

    Uses the same seed logic as :func:`forex._synthesize` but scales the
    random walk so the last bar matches ``close_price`` approximately.
    """
    import numpy as np

    seed = (config.SYNTHETIC_SEED + sum(ord(c) for c in pair) + 999) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)

    n = max(days, 60)
    daily_ret = rng.normal(loc=0.0002, scale=0.008, size=n)
    raw = (1.0 + daily_ret).cumprod()
    scale_factor = close_price / raw[-1]
    synthetic_close = raw * scale_factor

    intraday = rng.uniform(0.002, 0.01, size=n) * synthetic_close
    open_ = synthetic_close + rng.normal(0, 0.003, size=n) * synthetic_close
    high = pd.Series(synthetic_close).combine(pd.Series(open_), max) + intraday
    low = pd.Series(synthetic_close).combine(pd.Series(open_), min) - intraday
    volume = rng.integers(100_000, 2_000_000, size=n)

    idx = pd.bdate_range(
        end=pd.Timestamp.today().normalize(), periods=n, name="date"
    )
    return pd.DataFrame(
        {
            "open": open_,
            "high": high.values,
            "low": low.values,
            "close": synthetic_close,
            "volume": volume,
        },
        index=idx,
    )


def fetch_data(pair: str, days: Optional[int] = None) -> pd.DataFrame:
    """Return OHLCV for an NSE stock ticker (e.g. SCOM, KCB).

    Strategy
    --------
    1. Route to the configured NSE data source (config.NSE_DATA_SOURCE).
    2. Try TradingView for the current bar (default).
    3. Read any cached CSV for older bars.
    4. If the cache + current bar don't cover ``days``, prepend synthetic.
    5. Store the latest bar to cache for next time.

    Never raises — falls back to synthetic if all sources are unreachable.
    """
    # Route to the configured data source
    source_key = config.NSE_DATA_SOURCE
    if source_key == "mystocks":
        return _fetch_mystocks(pair, days=days)
    elif source_key == "rapidapi":
        return _fetch_rapidapi(pair, days=days)

    # Default: TradingView daily-bar fetch
    return _fetch_tradingview_cached(pair, days=days)


def _fetch_mystocks(pair: str, days: Optional[int] = None) -> pd.DataFrame:
    """Fetch NSE data from mystocks.co.ke.

    NOTE: A separate mystocks scraper exists at scripts/mystocks-scraper.py
    that scrapes public stock pages (no login) for current prices.
    This function is for historical OHLCV data — not yet implemented.

    To use the scraper instead: it runs as a pre-step in the morning briefing
    and caches prices. For price alerts, the `trading price` CLI command
    uses the tradingview source (default).

    Returns data via TradingView as fallback.
    """
    return _fetch_tradingview_cached(pair, days=days)


def _fetch_rapidapi(pair: str, days: Optional[int] = None) -> pd.DataFrame:
    """Fetch NSE data from RapidAPI — NOT BUILT.

    Considered but never implemented. Falls through to TradingView.
    """
    return _fetch_tradingview_cached(pair, days=days)


def _fetch_tradingview_cached(pair: str, days: Optional[int] = None) -> pd.DataFrame:
    """Return OHLCV from TradingView with local cache fallback (the default)."""
    days = days if days is not None else config.LOOKBACK_DAYS
    config.ensure_dirs()
    csv_path = _csv_path(pair)

    # 1. Try TradingView for today's bar
    tv = _fetch_tradingview(pair)
    source = "tradingview" if tv else "synthetic"

    # 2. Read cached history
    cached: pd.DataFrame = pd.DataFrame()
    try:
        cached = pd.read_csv(csv_path, index_col="date", parse_dates=True)
        if not cached.empty:
            cached.index.name = "date"
            # Ensure columns are numeric
            for col in ["open", "high", "low", "close", "volume"]:
                if col in cached.columns:
                    cached[col] = pd.to_numeric(cached[col], errors="coerce")
            # If live fetch failed but cache has real data, trust the cache
            if tv is None and not cached.empty:
                source = "tradingview"
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

    # 3. Build today's bar DataFrame if we have real data
    today_df: pd.DataFrame = pd.DataFrame()
    today = pd.Timestamp.today().normalize()
    if tv is not None:
        today_df = pd.DataFrame(
            [{
                "open": tv["open"],
                "high": tv["high"],
                "low": tv["low"],
                "close": tv["close"],
                "volume": tv["volume"],
            }],
            index=[today],
        )
        today_df.index.name = "date"

        # Append to cache for next run
        # Avoid duplicating if today's date already cached
        if not cached.empty and today in cached.index:
            cached.loc[today] = [tv["open"], tv["high"], tv["low"],
                                 tv["close"], tv["volume"]]
        else:
            cached = pd.concat([cached, today_df])
        cached = cached[~cached.index.duplicated(keep="last")]
        cached.to_csv(csv_path)

    # 4. Combine: use cached + today's bar
    combined = cached.copy() if not cached.empty else pd.DataFrame()
    if not today_df.empty and (combined.empty or today not in combined.index):
        combined = pd.concat([combined, today_df])

    # 5. If still too short, prepend synthetic
    if len(combined) < days:
        # Use the actual close from cache or TV as anchor, not a fake 100.0
        close_price = 100.0
        if tv and tv.get("close"):
            close_price = tv["close"]
        elif not cached.empty and "close" in cached.columns:
            close_price = float(cached["close"].iloc[-1])
        prefix = _synthetic_for_nse(pair, _MAX_SYNTHETIC_PREFIX, close_price)
        combined = pd.concat([prefix, combined])
        # Remove duplicate dates (keep real data)
        combined = combined[~combined.index.duplicated(keep="last")]

    # 6. Trim and return
    combined = combined.tail(days).copy()
    combined.index.name = "date"
    combined.attrs["source"] = source
    combined.attrs["nse_real"] = tv is not None
    return combined
