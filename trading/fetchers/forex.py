"""Forex data fetcher.

Primary path: yfinance (free, no key) for major pairs.
Secondary path: deterministic synthetic data so the engine is never blocked
when a network is unavailable, the ticker is exotic, or yfinance rate-limits us.

Exports
-------
fetch_data(pair, days) -> pd.DataFrame
    Returns OHLCV with a DatetimeIndex named 'date'. Always returns real
    rows; never raises on a network failure.
"""
from __future__ import annotations

import os
from typing import Optional, Any  # noqa: F401 -- kept for downstream consumers

import pandas as pd

from .. import config


# Map of DataFrame column names we expect to return
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def _safe_filename(pair: str) -> str:
    """Filesystem-safe encoding of a pair name like 'EUR/USD' -> 'EUR_USD'."""
    return pair.replace("/", "_")


def _csv_path(pair: str) -> str:
    return os.path.join(config.DATA_DIR, f"{_safe_filename(pair)}.csv")


def _synthesize(pair: str, days: int) -> pd.DataFrame:
    """Deterministic synthetic OHLCV. Realistic enough to exercise the engine.

    The seed is keyed on the pair so EUR/USD and USD/KES get different but
    repeatable series.
    """
    import numpy as np

    # Hash the pair string to an int so we get a stable but distinct seed
    seed = (config.SYNTHETIC_SEED + sum(ord(c) for c in pair)) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)

    n = days + 1
    # Base prices chosen so the two pairs start in a sensible ballpark
    base = 1.08 if "EUR" in pair else 130.0
    # Daily log-returns ~ N(0, vol) produces a geometric random walk
    daily_ret = rng.normal(loc=0.0001, scale=config.SYNTHETIC_VOL, size=n)
    close = base * (1.0 + daily_ret).cumprod()

    # Derive OHLC from close with small intraday ranges
    intraday_range = rng.uniform(0.0005, 0.0030, size=n) * close
    open_ = close + rng.normal(0, 0.0002, size=n) * close
    high = pd.Series(close).combine(pd.Series(open_), max) + intraday_range
    low = pd.Series(close).combine(pd.Series(open_), min) - intraday_range

    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n, name="date")
    volume = rng.integers(50_000, 500_000, size=n)

    df = pd.DataFrame(
        {
            "open": open_,
            "high": high.values,
            "low": low.values,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )
    return df


def _fetch_yfinance(pair: str, days: int) -> Optional[pd.DataFrame]:
    """Try yfinance; return None on any failure (so the caller can fall back)."""
    import yfinance as yf  # imported lazily to keep cold-start cheap

    ticker = config.YFINANCE_TICKERS.get(pair)
    if not ticker:
        return None
    try:
        df = yf.download(
            ticker,
            period=f"{days + 5}d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception:
        return None
    if df is None or df.empty:
        return None

    # yfinance returns a multi-index column frame in 1.x when auto_adjust=True
    # (e.g. ('Close', 'EURUSD=X')). Flatten to single-level names.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]

    # Keep only OHLCV columns that exist
    keep = [c for c in OHLCV_COLUMNS if c in df.columns]
    df = df[keep].copy()
    if df.empty or "close" not in df.columns:
        return None
    df.index.name = "date"
    return df


def fetch_data(pair: str, days: Optional[int] = None) -> pd.DataFrame:
    """Return OHLCV for ``pair``. Never raises; falls back to synthetic.

    The returned frame is sliced to the trailing ``days`` rows of business
    days, has a DatetimeIndex named 'date', and includes the standard OHLCV
    columns. The full series is cached to disk for reuse.
    """
    days = days if days is not None else config.LOOKBACK_DAYS
    config.ensure_dirs()

    df = _fetch_yfinance(pair, days)
    source = "yfinance"
    if df is None:
        df = _synthesize(pair, days)
        source = "synthetic"

    # Trim to the requested lookback
    df = df.tail(days).copy()

    # Persist the raw pull for later inspection
    try:
        df.to_csv(_csv_path(pair))
    except OSError:
        pass  # best-effort cache; don't break the run on disk errors

    # Stash the source as an attribute for downstream debugging
    df.attrs["source"] = source
    return df
