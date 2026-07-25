"""Resilience primitives: bounded timeout and exponential backoff.

``call_with_timeout`` runs a (potentially blocking) callable in a worker
thread and joins with a deadline. **It does not kill the worker** — Python
cannot safely force-kill a thread. Instead it returns a *timeout* sentinel and
leaves the worker to finish. Callers MUST treat a timeout as "outcome unknown"
and verify the real result via ``get_order_status`` / the order store. Never
blindly retry a mutating call after a timeout, or you risk a double-fill.

``with_exponential_backoff`` retries an idempotent call with capped
exponential delay and full jitter. The *call* is expected to be safe to retry
because the engine layers client-order-id dedup on top — but timeouts are
deliberately NOT retried as mutations; they are reconciled instead.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Optional, Tuple, TypeVar

T = TypeVar("T")

# Result tuple from call_with_timeout:
#   (completed: bool, result: Optional[T], error: str)
TimeoutResult = Tuple[bool, Optional[T], str]


def call_with_timeout(
    func: Callable[[], T],
    timeout: float,
    *,
    daemon: bool = True,
) -> TimeoutResult:
    """Run ``func`` in a daemon worker thread; return after ``timeout`` seconds.

    Returns
    -------
    (True, result, "")           — completed normally, result is the value.
    (False, None, "timeout")     — did not finish in time (worker still running).
    (False, None, "<repr>")      — worker raised; error string is repr(exc).

    The worker is never forcibly terminated. Its side effects (e.g. a broker
    fill that completed just after the deadline) are reconciled later by the
    engine via ``get_order_status``.
    """
    box: dict[str, Any] = {}
    err: dict[str, BaseException] = {}

    def _run() -> None:
        try:
            box["r"] = func()
        except BaseException as exc:  # noqa: BLE001 — capture any worker failure
            err["e"] = exc

    worker = threading.Thread(target=_run, daemon=daemon)
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        return (False, None, "timeout")
    if "e" in err:
        return (False, None, repr(err["e"]))
    return (True, box.get("r"), "")


def with_exponential_backoff(
    func: Callable[[], T],
    *,
    max_retries: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 5.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry ``func`` up to ``max_retries`` times with capped exponential backoff.

    Total attempts = ``max_retries + 1``. Raises the last exception if all
    attempts fail. ``sleep`` is injectable for tests.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 — retry any transient error
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = min(max_delay, base_delay * (backoff_factor ** attempt))
            if jitter:
                # Full jitter: spread retries so concurrent callers don't sync.
                delay = delay * (0.5 + random.random())
            sleep(delay)
    assert last_exc is not None
    raise last_exc


def execute_resilient(
    func: Callable[[], T],
    *,
    timeout: float,
    max_retries: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 5.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> TimeoutResult:
    """Compose timeout + backoff into one call.

    Each attempt is wrapped in ``call_with_timeout``. On a worker *exception*
    (not a timeout) the call is retried with backoff. A *timeout* is returned
    immediately as unknown — the caller reconciles rather than retrying a
    mutation that may have landed.
    """

    def _attempt() -> TimeoutResult:
        return call_with_timeout(func, timeout)

    completed, result, err = _attempt()
    attempts = 0
    while (not completed) and err != "timeout" and attempts < max_retries:
        if jitter:
            delay = min(max_delay, base_delay * (backoff_factor ** attempts)) * (0.5 + random.random())
        else:
            delay = min(max_delay, base_delay * (backoff_factor ** attempts))
        sleep(delay)
        completed, result, err = _attempt()
        attempts += 1

    return (completed, result, err)
