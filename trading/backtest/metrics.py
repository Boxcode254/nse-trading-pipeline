"""Additional performance and risk metrics for backtest results.

This module contains standalone metric functions that can also be
imported by the main engine.
"""
from __future__ import annotations

import numpy as np


def compute_max_drawdown(equity_curve: list[float]) -> float:
    """Return the maximum peak-to-trough drawdown as a fraction."""
    if len(equity_curve) < 2:
        return 0.0
    arr = np.array(equity_curve)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / peak
    return abs(float(np.min(dd)))


def compute_sharpe(daily_returns: list[float], annual_factor: int = 252) -> float:
    """Annualised Sharpe ratio from a series of daily returns."""
    arr = np.array(daily_returns)
    if arr.std() == 0 or len(arr) < 2:
        return 0.0
    return float(np.mean(arr) / np.std(arr) * np.sqrt(annual_factor))
