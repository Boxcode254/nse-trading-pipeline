"""User-facing report templates.

Each module assembles narratives from :mod:`trading.narratives` into
a single string suitable for the CLI / Telegram / web dashboard.
Templates are intentionally pure (no I/O) so they can be unit-tested
in isolation.
"""
from . import (
    brief,
    opportunities,
    portfolio,
    signal,
    summary,
    warnings,
)

__all__ = [
    "brief",
    "opportunities",
    "portfolio",
    "signal",
    "summary",
    "warnings",
]
