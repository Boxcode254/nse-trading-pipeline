"""
PAPER ENGINE — ARCHIVED 2026-07-17

This module has been retired. The paper trading engine (portfolio management,
signal execution, position tracking) was running as a separate, disconnected
system alongside the auto-trader with no connectivity to state.json.

The code is preserved at ~/.trading/archive/paper_engine.py for reference.

The weight-adjustment concept (signal_profiler + rule_updater) is worth
rebuilding deliberately — wired to state.json, with hardcoded dry_run=True
and Telegram-based human approval for all proposed changes.
"""

import logging

logger = logging.getLogger(__name__)


class PaperTradingEngine:
    """Archived stub — see ~/.trading/archive/paper_engine.py for original code."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "PaperTradingEngine has been archived (2026-07-17). "
            "The paper engine was a separate, disconnected trading system "
            "running alongside the auto-trader. It has been retired for safety. "
            "See ~/.trading/archive/paper_engine.py for reference."
        )


def create_engine(*args, **kwargs):
    """Archived factory — see ~/.trading/archive/paper_engine.py."""
    raise RuntimeError(
        "create_engine() has been archived (2026-07-17). "
        "The paper engine was a separate, disconnected trading system. "
        "It has been retired for safety. "
        "See ~/.trading/archive/paper_engine.py for reference."
    )
