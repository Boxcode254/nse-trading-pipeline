"""Market data service.

Thin facade over the fetcher dispatch in ``trading.fetchers``. Returns
plain dicts so the CLI and other callers can format freely.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import pandas as pd

from .. import config
from ..fetchers import fetch_data


def fetch_all() -> dict[str, pd.DataFrame]:
    """Fetch OHLCV for every configured pair.

    Returns a ``{pair: DataFrame}`` dict. Failures are skipped (the
    DataFrame is omitted from the result); the caller can detect
    missing pairs by comparing the keys to ``config.PAIRS``.
    """
    config.ensure_dirs()
    out: dict[str, pd.DataFrame] = {}
    for pair in config.PAIRS:
        try:
            df = fetch_data(pair)
        except Exception:  # noqa: BLE001
            continue
        if df is None or df.empty:
            continue
        out[pair] = df
    return out


def fetch_one(pair: str, days: Optional[int] = None) -> pd.DataFrame:
    """Fetch OHLCV for a single pair. May return an empty DataFrame."""
    return fetch_data(pair, days=days)


def latest_price(pair: str) -> dict[str, Any]:
    """Return the most recent close + a few summary stats for *pair*.

    Output::

        {
          "symbol": "SCOM",
          "date": "2026-06-27",
          "price": 23.45,
          "previous_close": 23.10,
          "change_abs": 0.35,
          "change_pct": 1.5,
          "source": "yfinance",
        }
    """
    df = fetch_one(pair)
    if df is None or df.empty:
        return {
            "symbol": pair,
            "date": "",
            "price": None,
            "previous_close": None,
            "change_abs": None,
            "change_pct": None,
            "source": "?",
        }
    last = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-2]) if len(df) > 1 else last
    change_abs = last - prev
    change_pct = (change_abs / prev * 100.0) if prev else 0.0
    return {
        "symbol": pair,
        "date": pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d"),
        "price": round(last, 6),
        "previous_close": round(prev, 6),
        "change_abs": round(change_abs, 6),
        "change_pct": round(change_pct, 4),
        "source": df.attrs.get("source", "?"),
    }


def asset_snapshot(pair: str) -> dict[str, Any]:
    """Lightweight snapshot of an asset — used by the price command.

    Includes current price, recent trend (20d/50d SMA), and a naive
    volatility estimate (annualised 20d std of returns).
    """
    df = fetch_one(pair)
    if df is None or df.empty or "close" not in df.columns:
        return {"symbol": pair, "status": "no_data"}

    close = df["close"].astype(float)
    last = float(close.iloc[-1])
    sma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    sma50 = float(close.tail(50).mean()) if len(close) >= 50 else None
    trend = (
        "up" if sma20 and sma50 and sma20 > sma50
        else "down" if sma20 and sma50 and sma20 < sma50
        else "flat"
    )
    daily_ret = close.pct_change().dropna()
    vol_pct = float(daily_ret.tail(20).std() * (252 ** 0.5) * 100.0) if len(daily_ret) >= 20 else None

    return {
        "symbol": pair,
        "status": "ok",
        "date": pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d"),
        "price": round(last, 6),
        "sma_20": round(sma20, 6) if sma20 else None,
        "sma_50": round(sma50, 6) if sma50 else None,
        "trend": trend,
        "annualised_volatility_pct": round(vol_pct, 2) if vol_pct is not None else None,
    }


def measure_fetch_time() -> float:
    """Measure the wall-clock time to fetch all configured pairs. Used by health."""
    t0 = time.time()
    fetch_all()
    return time.time() - t0
