"""
SIGNAL PROFILER — ARCHIVED 2026-07-17

This module has been retired. It was part of the paper engine system that
fed signal performance metrics (win rate, consistency, drawdown) into the
rule_updater for weight auto-tuning.

The concept is worth rebuilding deliberately — wired to the auto-trader's
state.json outcome history instead of the paper engine's empty decisions.db.
But only as a proposal engine, never as an auto-applier.

Code preserved at ~/.trading/archive/signal_profiler.py for reference.
"""

import logging

logger = logging.getLogger(__name__)


class SignalProfiler:
    """Archived stub — see ~/.trading/archive/signal_profiler.py."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "SignalProfiler has been archived (2026-07-17). "
            "Part of the retired paper engine system. "
            "See ~/.trading/archive/signal_profiler.py for reference."
        )


def create_profiler(*args, **kwargs):
    """Archived factory."""
    raise RuntimeError(
        "create_profiler() has been archived (2026-07-17)."
    )


class SignalMetrics:
    """Archived — preserved for type references in archive code only."""
    def __init__(self):
        pass
