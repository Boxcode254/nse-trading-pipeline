"""Source registry + per-source rate limit tracking.

The market-intel layer talks to several external sources (Alpha
Vantage, Finviz RSS, Google News RSS, etc.). Each source has its
own rate limit, and we want a single place to register / inspect
them.

The rate limit here is *advisory* — it tracks how many calls we've
made in the last 60 seconds and refuses to issue a new one when
the source is exhausted. The actual HTTP call is still the
caller's responsibility; this module just decides whether to
attempt it.

Default behaviour
-----------------

- No rate limit (``rate_limit_per_minute=0``) → always allowed
- Otherwise: simple sliding-window counter in memory. If the
  process restarts the counter resets — fine for our use case
  (cron scanner, runs every 4 hours).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Dict, Optional


class Registry:
    """In-memory registry of news / data sources.

    Use ``register(name, fn, rate_limit_per_minute=N)`` to add a
    source. ``get(name)`` retrieves the callable. ``allowed(name)``
    and ``mark_used(name)`` gate calls.

    This is intentionally a simple, in-memory object — the spec
    calls for a "source registry + rate limit management", not a
    distributed system. Construct a fresh Registry per process.
    """

    def __init__(self) -> None:
        self._fns: Dict[str, Callable] = {}
        self._limits: Dict[str, int] = {}
        self._calls: Dict[str, Deque[float]] = {}

    # ── Registration ───────────────────────────────────────────────

    def register(
        self,
        name: str,
        fn: Callable,
        rate_limit_per_minute: int = 0,
    ) -> None:
        """Add a source. Replaces any existing entry with the same name."""
        self._fns[name] = fn
        self._limits[name] = max(0, int(rate_limit_per_minute))
        self._calls.setdefault(name, deque())

    def unregister(self, name: str) -> None:
        self._fns.pop(name, None)
        self._limits.pop(name, None)
        self._calls.pop(name, None)

    def get(self, name: str) -> Optional[Callable]:
        return self._fns.get(name)

    def list(self) -> list[str]:
        """Names of all registered sources."""
        return sorted(self._fns.keys())

    # ── Rate limiting ──────────────────────────────────────────────

    def allowed(self, name: str) -> bool:
        """Return True if a new call to ``name`` is permitted.

        Sources with no rate limit (``rate_limit_per_minute=0``) are
        always allowed. Otherwise, return False once the limit is
        reached within a 60-second sliding window.
        """
        limit = self._limits.get(name, 0)
        if limit <= 0:
            return True
        window = self._calls.get(name)
        if window is None:
            return True
        # Drop timestamps older than 60 seconds
        self._prune(name, window)
        return len(window) < limit

    def mark_used(self, name: str) -> None:
        """Record one call to ``name`` (for rate-limit accounting)."""
        if self._limits.get(name, 0) <= 0:
            return
        window = self._calls.setdefault(name, deque())
        self._prune(name, window)
        window.append(time.time())

    def remaining(self, name: str) -> int:
        """How many more calls to ``name`` are permitted right now."""
        limit = self._limits.get(name, 0)
        if limit <= 0:
            return -1  # unlimited
        window = self._calls.get(name, deque())
        self._prune(name, window)
        return max(0, limit - len(window))

    # ── Internals ──────────────────────────────────────────────────

    @staticmethod
    def _prune(name: str, window: Deque[float]) -> None:
        cutoff = time.time() - 60.0
        while window and window[0] < cutoff:
            window.popleft()


# Module-level convenience registry — most callers want the shared
# instance, not a per-call one. Tests construct their own Registry
# to avoid shared state.
registry = Registry()
