"""
OUTCOME REVIEWER — ARCHIVED 2026-07-17

This module was the hourly paper-trade outcome reviewer. It closed expired
positions, calculated PnL, and updated signal scores.

It depended on the paper_engine system (paper_engine.py, rule_updater.py,
signal_profiler.py, learning/db.py) which has been archived for safety.
The causal chain: paper_engine archived → outcome_reviewer cannot resolve
its imports → outcome-reviewer-cron is meaningless even if un-paused.

Decision: formally retire alongside paper_engine. Outcome tracking can be
deliberately rebuilt against state.json + take_snapshot in the future.
The same rebuilder rules apply (hardcoded dry_run, Telegram approval,
propose-don't-apply).

Code preserved at ~/.trading/archive/outcome_reviewer.py.
"""

import logging
import sys

logger = logging.getLogger(__name__)


class OutcomeReviewer:
    """Archived stub — see ~/.trading/archive/outcome_reviewer.py for original code."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "OutcomeReviewer has been archived (2026-07-17). "
            "It depended on the paper engine system that has been retired for safety. "
            "See ~/.trading/archive/outcome_reviewer.py for reference."
        )


class PriceFeed:
    """Archived stub."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "PriceFeed has been archived (2026-07-17). "
            "Part of the retired outcome reviewer. "
            "See ~/.trading/archive/outcome_reviewer.py for reference."
        )


class ReviewResult:
    """Archived dataclass — kept for type references only."""

    def __init__(self, *args, **kwargs):
        pass


class SignalTrend:
    """Archived dataclass — kept for type references only."""

    def __init__(self, *args, **kwargs):
        pass


def main(*args, **kwargs):
    """Archived — raises informative error."""
    raise RuntimeError(
        "outcome_reviewer.main() has been archived (2026-07-17). "
        "It depended on the paper engine system. "
        "See ~/.trading/archive/outcome_reviewer.py for reference."
    )


if __name__ == "__main__":
    sys.exit(main())
