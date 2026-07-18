"""Pluggable strategy framework for the trading research platform.

Every strategy subclasses :class:`BaseStrategy` and implements
:meth:`generate_signals`, which must return a ``pd.Series``
of ``'BUY'``, ``'SELL'``, or ``'HOLD'`` for every row in the
input DataFrame.

The backtesting engine accepts any strategy object — no code
changes needed to test a new idea.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class StrategyMeta:
    """Metadata frozen at strategy registration time."""
    name: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"


class BaseStrategy(ABC):
    """Subclass this to create a tradable strategy.

    The lifecycle is:

    1. ``prepare(df)`` — add indicator columns to *df* (in-place).
       Default is a no-op; override if your strategy needs SMA, RSI,
       MACD, Bollinger Bands, etc.
    2. ``generate_signals(df)`` — return a ``pd.Series`` with one of
       ``'BUY'``, ``'SELL'``, ``'HOLD'`` for **every** row.
    3. Backtest engine walks the signal series, tracks positions,
       and computes trades + equity curve.
    """

    meta: StrategyMeta

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pre-compute any indicator columns needed.

        Override this method.  Add columns to *df* in-place and
        return it so the caller sees the enriched DataFrame.
        """
        return df

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Return ``'BUY'`` / ``'SELL'`` / ``'HOLD'`` for every row.

        The returned Series must have the same index as *df*.
        """
        ...

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def description(self) -> str:
        return self.meta.description

    def __str__(self) -> str:
        return f"{self.meta.name} v{self.meta.version}"
