"""
LEARNING DATABASE — ARCHIVED 2026-07-17

This module provided the SQLite database layer for the retired paper engine
system (paper_engine, rule_updater, signal_profiler). It has been archived
for safety — a dormant capability with a live trigger condition is a landmine.

All functions return graceful empty defaults so downstream consumers (e.g.
dashboard-gen.py) degrade instead of crash.

The concept (signal → weight adjustment via win rate/consistency/drawdown)
is worth rebuilding deliberately — wired to state.json, with hardcoded
dry_run=True and Telegram-based human approval for all proposed changes.

Code preserved at ~/.trading/archive/learning/db.py for reference.
The standalone learning system at ~/.trading/learning/ is unaffected.
"""

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DECISIONS_DB_PATH = None  # Archived — no longer used


class _EmptyRow:
    """Subscriptable empty row — returns 0 for any numeric index so callers
    like ``total_counts[0]`` and ``row[1]`` don't crash with NoneType errors."""

    def __getitem__(self, idx):
        return 0


class _MockCursor:
    """Returns empty results so callers degrade gracefully."""

    def execute(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter([])

    def fetchone(self):
        return _EmptyRow()

    def fetchall(self):
        return []

    def close(self):
        pass


class _MockConnection:
    """Context-manager connection that returns empty cursors."""

    row_factory = None

    def cursor(self):
        return _MockCursor()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@contextmanager
def get_conn(*args, **kwargs):
    """Return empty connection — see ~/.trading/archive/learning/db.py."""
    yield _MockConnection()


@contextmanager
def get_connection(*args, **kwargs):
    """Return empty connection — see ~/.trading/archive/learning/db.py."""
    yield _MockConnection()


def init_db(*args, **kwargs):
    """Archived — no-op."""
    pass


def add_decision(*args, **kwargs):
    """Archived — no-op."""
    pass


def get_decision(*args, **kwargs):
    """Archived — returns None."""
    return None


def get_open_decisions(*args, **kwargs):
    """Archived — returns empty list."""
    return []


def add_outcome(*args, **kwargs):
    """Archived — no-op."""
    pass


def get_outcome(*args, **kwargs):
    """Archived — returns None."""
    return None


def update_decision_status(*args, **kwargs):
    """Archived — no-op."""
    pass


def add_rule_version(*args, **kwargs):
    """Archived — no-op."""
    pass


def get_rule_version(*args, **kwargs):
    """Archived — returns empty dict."""
    return {}


def get_latest_rule_version(*args, **kwargs):
    """Archived — returns empty dict."""
    return {}
