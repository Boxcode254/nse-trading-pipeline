"""Atomic run-lock for the auto-trader.

The auto-trader is fired by cron at 10:30 EAT. If a previous run is still
executing (slow market data, a hung Mansa call, an over-long replay) the next
cron tick would start a *second* process that reads the same portfolio state
and double-fills the plan. This lock guarantees at most one live run.

Implementation: an ``fcntl.flock(LOCK_EX | LOCK_NB)`` on a lock file. This is
atomic across processes on the same host and is automatically released by the
kernel when the holding process exits (crash/segv/OOM) — unlike a stale PID
file, there is no "forgot to clean up" window.

Usage::

    lock = RunLock()              # or RunLock(lock_path=..., holder="auto-trader")
    if not lock.acquire():
        print("already running (pid/stale holder) — exiting")
        sys.exit(0)
    try:
        run_auto_trade(...)
    finally:
        lock.release()
"""
from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path
from typing import Optional


DEFAULT_LOCK_PATH = os.path.expanduser("~/.trading/execution/auto_trader.lock")


class RunLock:
    """Exclusive non-blocking run lock backed by flock."""

    def __init__(self, lock_path: str = DEFAULT_LOCK_PATH, holder: str = "auto-trader"):
        self.lock_path = Path(lock_path)
        self.holder = holder
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """Try to take the exclusive lock. Returns True on success, False if
        another process already holds it."""
        if self._fd is not None:
            return True  # idempotent: already held by this instance
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            # O_CREAT so the file is created if missing; O_RDWR required for flock.
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError as exc:
            # If we cannot even open the lock file, refuse to run rather than
            # risk a double-fill without protection.
            sys.stderr.write(f"[RunLock] cannot open lock file {self.lock_path}: {exc}\n")
            return False

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            self._fd = None
            return False

        # Write holder identity (best-effort, non-fatal if it fails).
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{self.holder} pid={os.getpid()}\n".encode())
            os.fsync(fd)
        except OSError:
            pass
        self._fd = fd
        return True

    def is_locked(self) -> bool:
        """Peek whether another process currently holds the lock."""
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError:
            return True  # assume locked if we can't even open
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # We got it — so it was NOT locked. Release and report False.
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except OSError:
            return True
        finally:
            os.close(fd)

    def release(self) -> None:
        """Release the lock. Safe to call if not held."""
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        except OSError:
            pass
        finally:
            self._fd = None

    def __enter__(self) -> "RunLock":
        if not self.acquire():
            raise RuntimeError(f"RunLock already held: {self.lock_path}")
        return self

    def __exit__(self, *exc) -> None:
        self.release()
