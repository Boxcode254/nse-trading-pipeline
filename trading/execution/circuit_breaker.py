"""Circuit breaker for broker connectivity.

The breaker sits between the execution engine and the broker. When broker
calls fail repeatedly (timeouts, connection resets, 5xx), the breaker trips to
``OPEN`` and **fails fast** for a cooldown window instead of hammering a
broken broker — which on a real broker could queue duplicate orders or rack up
fees. After the cooldown it moves to ``HALF_OPEN`` and allows a single probe;
success closes it, failure re-opens it.

State is persisted to disk so a process restart (cron overlap, deploy) does not
reset the breaker and immediately retry a broker that is still down.

This module is intentionally free of broker specifics — it only counts
failures and exposes ``allow()`` / ``on_success()`` / ``on_failure()``.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_BREAKER_PATH = os.path.expanduser("~/.trading/execution/circuit_breaker.json")


class CircuitBreakerError(Exception):
    """Raised when the breaker is OPEN and a call is attempted."""


class CircuitBreaker:
    """A persisted, three-state circuit breaker.

    States:
        CLOSED   — normal; calls pass through, failures are counted.
        OPEN     — tripped; calls fail fast until cooldown elapses.
        HALF_OPEN — one probe call allowed; success -> CLOSED, failure -> OPEN.
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        half_open_max: int = 1,
        state_path: str = DEFAULT_BREAKER_PATH,
        clock: Optional[callable] = None,  # injectable for tests
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max = half_open_max
        self.state_path = Path(state_path)
        self._clock = clock or time.monotonic
        self._state = self._load()

    # ── Persistence ───────────────────────────────────────────────────
    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
                # Validate shape; reset if corrupt.
                if all(k in data for k in ("state", "failures", "opened_at", "half_open_count")):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "state": self.CLOSED,
            "failures": 0,
            "opened_at": 0.0,
            "half_open_count": 0,
        }

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self.state_path)

    # ── State helpers ─────────────────────────────────────────────────
    @property
    def state(self) -> str:
        return self._state["state"]

    def _maybe_recover_from_open(self) -> None:
        """If cooldown elapsed while OPEN, move to HALF_OPEN."""
        if self._state["state"] == self.OPEN:
            if self._clock() - self._state["opened_at"] >= self.cooldown_seconds:
                self._state["state"] = self.HALF_OPEN
                self._state["half_open_count"] = 0
                self._save()

    def allow(self) -> bool:
        """Return True if a call is permitted; may transition OPEN->HALF_OPEN."""
        self._maybe_recover_from_open()
        if self._state["state"] == self.CLOSED:
            return True
        if self._state["state"] == self.HALF_OPEN:
            return self._state["half_open_count"] < self.half_open_max
        return False  # OPEN

    def on_success(self) -> None:
        """Record a successful call — closes the breaker."""
        if self._state["state"] in (self.HALF_OPEN, self.OPEN):
            self._state["failures"] = 0
            self._state["half_open_count"] = 0
            self._state["state"] = self.CLOSED
            self._save()
        elif self._state["state"] == self.CLOSED:
            # Only reset counter if it had accumulated; keep CLOSED.
            if self._state["failures"] != 0:
                self._state["failures"] = 0
                self._save()

    def on_failure(self) -> None:
        """Record a failed call — may open the breaker."""
        if self._state["state"] == self.HALF_OPEN:
            self._state["state"] = self.OPEN
            self._state["opened_at"] = self._clock()
            self._state["failures"] += 1
            self._save()
            return
        if self._state["state"] == self.CLOSED:
            self._state["failures"] += 1
            if self._state["failures"] >= self.failure_threshold:
                self._state["state"] = self.OPEN
                self._state["opened_at"] = self._clock()
                self._save()
            else:
                self._save()

    def trip(self) -> None:
        """Force-open the breaker (e.g. repeated timeout despite retries)."""
        self._state["state"] = self.OPEN
        self._state["opened_at"] = self._clock()
        self._save()

    def reset(self) -> None:
        """Force-close the breaker (manual recovery)."""
        self._state = {
            "state": self.CLOSED,
            "failures": 0,
            "opened_at": 0.0,
            "half_open_count": 0,
        }
        self._save()

    def snapshot(self) -> dict:
        return dict(self._state)
