"""Historical backtesting for the trading signal strategy.

Usage
-----
    python3 -m trading backtest                  # all pairs, 2-year default
    python3 -m trading backtest --pairs SCOM,KCB  # specific pairs
    python3 -m trading backtest --years 5         # longer window
    python3 -m trading backtest --benchmark       # compare vs buy-and-hold
"""
from .engine import run_backtest
from .report import format_backtest_results

__all__ = ["run_backtest", "format_backtest_results"]
