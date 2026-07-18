"""Services layer — pure business logic, no CLI concerns.

The CLI calls these; the dashboard (future) and REST endpoints (future)
will also call these. Each service function is a thin facade over the
existing engine modules, returning plain dicts so callers can format
freely.
"""
from . import advisor, backtest, decision, health, market, market_intel, portfolio, ranking, signal, stats, strategies

__all__ = ["advisor", "backtest", "decision", "health", "market", "market_intel",
           "portfolio", "ranking", "signal", "stats", "strategies"]
