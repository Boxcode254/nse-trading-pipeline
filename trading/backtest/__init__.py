"""NSE Backtester package.

Re-exports the public entry points used by the trading CLI and sibling
modules. Keep this list in sync with names imported elsewhere:
    - trading/__main__.py  -> run_backtest, format_backtest_results
    - trading/scripts/build_risk_cache.py -> fetch_history
"""

from .engine import run_backtest
from .report import format_backtest_results
from .fetch_history import fetch_history

__all__ = ["run_backtest", "format_backtest_results", "fetch_history"]
