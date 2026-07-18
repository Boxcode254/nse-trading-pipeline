"""Historical OHLCV data fetcher for backtesting.

Supports two backends:
- yfinance  → forex pairs (EUR/USD, USD/KES)
- tvDatafeed → NSE stocks (SCOM, KCB, …)
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .. import config


def fetch_history(pair: str, years: float = 2.0) -> pd.DataFrame:
    """Return daily OHLCV for *pair* covering roughly *years* of history.

    Returns a DataFrame with columns ``open, high, low, close, volume`` and a
    ``datetime``-type index named ``date``.  May be empty if the data source
    is unreachable.
    """
    asset_class = config.get_asset_class(pair)
    n_bars = max(int(years * 252) + 100, 500)  # ~252 trading days/year + buffer

    if asset_class == "forex":
        return _fetch_yfinance(pair, n_bars)
    return _fetch_tvdatafeed(pair, n_bars)


# ── yfinance backend ──────────────────────────────────────────────────

def _fetch_yfinance(pair: str, n_bars: int) -> pd.DataFrame:
    try:
        import yfinance as yf

        ticker = config.YFINANCE_TICKERS.get(pair, pair)
        df = yf.download(
            ticker,
            period=f"{n_bars // 21}mo",  # approximate in months
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        # yfinance returns multi-index columns sometimes; flatten
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename_axis("date")
        needed = {"Open", "High", "Low", "Close", "Volume"}
        if not needed.issubset(df.columns):
            return pd.DataFrame()
        out = pd.DataFrame({
            "open": df["Open"].astype(float).values,
            "high": df["High"].astype(float).values,
            "low": df["Low"].astype(float).values,
            "close": df["Close"].astype(float).values,
            "volume": df["Volume"].astype(float).values,
        }, index=df.index)
        return out.tail(n_bars)
    except Exception:
        return pd.DataFrame()


# ── tvDatafeed backend (TradingView) ──────────────────────────────────

def _fetch_tvdatafeed(pair: str, n_bars: int) -> pd.DataFrame:
    try:
        # The installed package name is case-sensitive on Linux
        import importlib
        tv_mod = importlib.import_module("tvDatafeed")
        TvDatafeed = tv_mod.TvDatafeed
        Interval = tv_mod.Interval

        tv = TvDatafeed()
        data = tv.get_hist(
            symbol=pair,
            exchange="NSEKE",
            interval=Interval.in_daily,
            n_bars=n_bars,
        )
        if data is None or data.empty:
            return pd.DataFrame()

        # tvDatafeed returns columns: symbol, open, high, low, close, volume
        # Index is datetime (includes time, e.g. 09:00 — we keep it)
        data = data.rename(
            columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        )
        data.index.name = "date"
        return data[["open", "high", "low", "close", "volume"]]
    except Exception:
        return pd.DataFrame()
