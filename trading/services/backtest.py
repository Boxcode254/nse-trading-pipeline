"""Backtest service.

Re-exports the backtest runner from the strategies service for naming
clarity. ``trading backtest`` calls this; ``trading compare`` calls the
comparison version.
"""
from __future__ import annotations

from .strategies import run, compare

__all__ = ["run", "compare"]
