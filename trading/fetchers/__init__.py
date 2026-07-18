"""Fetcher dispatcher — routes each pair to the correct data source.

- Pairs with ``/`` (e.g. ``EUR/USD``) → forex (yfinance)
- Stock tickers (no ``/``, e.g. ``SCOM``) → NSE (TradingView)
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .. import config


def fetch_data(pair: str, days: Optional[int] = None) -> pd.DataFrame:
    """Route to the correct fetcher based on the pair name."""
    asset_class = config.get_asset_class(pair)
    if asset_class == "forex":
        from . import forex
        return forex.fetch_data(pair, days=days)
    # stocks / NSE
    from . import nse
    return nse.fetch_data(pair, days=days)
