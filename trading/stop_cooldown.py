"""Stop-loss re-entry cooldown.

After the paper engine executes a FILLED stop-loss sell, the symbol enters a
cooldown: the target allocator and the auto-trader refuse to BUY it back for
N trading sessions. This closes the KNRE incident (2026-10-16 stop-out at
KES 4.0639, re-bought 2026-10-19 at KES 4.1462 purely on sector underweight)
where nothing remembered the stop.

State
-----
``~/.trading/portfolio/stop_cooldown.json``::

    {
      "version": 1,
      "entries": {
        "KNRE": {
          "exit_date": "2026-10-16",
          "exit_price": 4.0639,
          "shares": 1802,
          "sessions": 5,
          "recorded_at": "2026-10-19T07:31:00+00:00"
        }
      }
    }

Writes are atomic (tmp file + ``os.replace``), mode 0600, and a one-time
``.bak`` copy of the previous content is made before the first write.

Session counting
----------------
A cooldown is counted in TRADING SESSIONS. The repo contains NO NSE public
holiday list/calendar (searched: trading/, scripts/, data/ — only an
incidental comment in price_source.py), so sessions are weekdays Mon-Fri.
NSE public holidays are NOT excluded; the cooldown may therefore span a
holiday and stay active one wall-clock day longer than a holiday-aware
count would give. Fail-safe direction: blocks re-entry slightly longer,
never shorter.

Window semantics: the cooldown covers the N weekday sessions AFTER the exit
session. The last blocked session (``until``) is EXCLUSIVE — re-entry is
allowed the session AFTER ``until``. Example: exit Friday 2026-10-16 with
sessions=5 blocks Mon 19 through Fri 23 (until=2026-10-23); the first
allowed session is Mon 2026-10-26.

Fail-open guarantee: a missing or corrupt state file is treated as "no
cooldowns active", logs a WARNING, and NEVER raises — the 10:30 EAT run
must not crash on a bad state file.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_PATH = Path(os.path.expanduser("~/.trading/portfolio/stop_cooldown.json"))
STATE_VERSION = 1
DEFAULT_SESSIONS = 5


def default_sessions() -> int:
    """Cooldown length in sessions (env override ``STOP_COOLDOWN_SESSIONS``).

    Re-read on every call (never cached) so tests and operators can change
    it without a process restart. Invalid values fall back to the default.
    """
    raw = os.environ.get("STOP_COOLDOWN_SESSIONS")
    if raw is None:
        return DEFAULT_SESSIONS
    try:
        n = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "STOP_COOLDOWN_SESSIONS=%r is not an int — using default %d",
            raw, DEFAULT_SESSIONS,
        )
        return DEFAULT_SESSIONS
    if n < 1:
        logger.warning(
            "STOP_COOLDOWN_SESSIONS=%d < 1 — using default %d", n, DEFAULT_SESSIONS,
        )
        return DEFAULT_SESSIONS
    return n


def _today() -> date:
    """Today's date (UTC). Module-level so tests can pin it deterministically."""
    return datetime.now(timezone.utc).date()


def _parse_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _load_state(path: Optional[Path] = None) -> dict[str, Any]:
    """Load the cooldown state. Fail-open: missing/corrupt => {}, with WARN."""
    p = Path(path) if path else STATE_PATH
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning(
            "stop_cooldown state file %s unreadable/corrupt (%s) — treating as "
            "no active cooldowns (fail-open)", p, exc,
        )
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        logger.warning(
            "stop_cooldown state file %s has unexpected schema — treating as "
            "no active cooldowns (fail-open)", p,
        )
        return {}
    return {"version": data.get("version", STATE_VERSION), "entries": entries}


def _write_state(data: dict[str, Any], path: Optional[Path] = None) -> None:
    """Atomically write state: tmp file, mode 0600, os.replace; .bak first write."""
    p = Path(path) if path else STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    bak = p.with_name(p.name + ".bak")
    if p.exists() and not bak.exists():
        try:
            shutil.copy2(p, bak)
        except OSError as exc:
            logger.warning("could not create %s backup: %s", bak, exc)
    fd, tmp_name = tempfile.mkstemp(
        prefix=p.name + ".", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _weekday_sessions_between(start: date, end: date) -> int:
    """Count weekday sessions in the half-open window (start, end].

    start == end -> 0; counts Mon-Fri only (see module docstring: no NSE
    holiday calendar exists in this repo).
    """
    if end <= start:
        return 0
    days = (end - start).days
    n = 0
    for i in range(1, days + 1):
        if (start + timedelta(days=i)).weekday() < 5:
            n += 1
    return n


def record_stop_exit(
    symbol: str,
    exit_date: str,
    exit_price: float,
    shares: int,
    sessions: Optional[int] = None,
    path: Optional[Path] = None,
) -> bool:
    """Record a stop-loss exit for ``symbol``. Idempotent per (symbol, exit_date).

    A second call for the same (symbol, exit_date) pair neither duplicates nor
    extends the entry — it is a no-op. Returns True if a new entry was written,
    False if the call was an idempotent no-op.

    Fail-open: a corrupt/missing existing file is treated as no cooldowns
    (WARN) and the write proceeds with a fresh, valid state. OSError on write
    is logged and swallowed — recording a cooldown must never crash the
    trading run.
    """
    if sessions is None:
        sessions = default_sessions()
    try:
        sessions = max(1, int(sessions))
    except (TypeError, ValueError):
        sessions = default_sessions()

    exit_d = _parse_date(exit_date)
    if exit_d is None:
        logger.warning(
            "record_stop_exit: unparseable exit_date %r for %s — not recorded",
            exit_date, symbol,
        )
        return False

    state = _load_state(path)
    entries: dict[str, Any] = dict(state.get("entries") or {})
    existing = entries.get(symbol)
    if isinstance(existing, dict) and _parse_date(existing.get("exit_date")) == exit_d:
        return False  # idempotent: same (symbol, exit_date) already recorded

    entries[symbol] = {
        "exit_date": exit_d.isoformat(),
        "exit_price": float(exit_price),
        "shares": int(shares),
        "sessions": sessions,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _write_state({"version": STATE_VERSION, "entries": entries}, path)
    except OSError as exc:
        logger.warning(
            "could not persist stop_cooldown state (%s) — cooldown for %s "
            "not recorded", exc, symbol,
        )
        return False
    logger.info(
        "stop-loss cooldown recorded: %s exited %s @ %s (%d sessions)",
        symbol, exit_d.isoformat(), exit_price, sessions,
    )
    return True


def cooldown_status(
    symbol: str,
    on_date: Optional[str] = None,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Re-entry status for ``symbol`` as of ``on_date`` (default: today, UTC).

    Returns {"active": bool, "until": "<date>"|None, "exit_price": float|None,
    "sessions_remaining": int}. ``sessions_remaining`` counts the blocked
    session(s) left INCLUDING ``on_date`` when active (5 on the first blocked
    session of a 5-session cooldown, 1 on the last). ``until`` is the last
    blocked session and is EXCLUSIVE: re-entry is allowed the session after it.

    Fail-open: never raises; a corrupt/missing state file reports no cooldown.
    """
    inactive: dict[str, Any] = {
        "active": False,
        "until": None,
        "exit_price": None,
        "sessions_remaining": 0,
    }
    try:
        state = _load_state(path)
        entry = (state.get("entries") or {}).get(symbol)
        if not isinstance(entry, dict):
            return inactive
        exit_d = _parse_date(entry.get("exit_date"))
        if exit_d is None:
            logger.warning(
                "stop_cooldown entry for %s has unparseable exit_date %r — "
                "ignoring (fail-open)", symbol, entry.get("exit_date"),
            )
            return inactive
        raw_n = entry.get("sessions")
        if isinstance(raw_n, (int, float)) and not isinstance(raw_n, bool):
            n = max(1, int(raw_n))
        else:
            n = default_sessions()
        on_d = _parse_date(on_date) if on_date else _today()
        if on_d is None:
            on_d = _today()
        elapsed = _weekday_sessions_between(exit_d, on_d)
        # elapsed 0        -> exit session itself / future date: window not open
        # elapsed 1..N     -> blocked (session N is the exclusive `until`)
        # elapsed > N      -> window expired
        if elapsed < 1 or elapsed > n:
            return inactive
        # The date of the Nth weekday session after the exit.
        until = exit_d
        counted = 0
        while counted < n:
            until += timedelta(days=1)
            if until.weekday() < 5:
                counted += 1
        return {
            "active": True,
            "until": until.isoformat(),
            "exit_date": exit_d.isoformat(),
            "exit_price": float(entry.get("exit_price") or 0.0),
            "sessions_remaining": n - elapsed + 1,
        }
    except Exception as exc:  # noqa: BLE001 — never block the 10:30 run
        logger.warning(
            "cooldown_status(%s) failed (%s) — treating as no cooldown", symbol, exc,
        )
        return dict(inactive)


def list_cooldowns(
    on_date: Optional[str] = None,
    path: Optional[Path] = None,
) -> dict[str, dict[str, Any]]:
    """All recorded cooldown entries with their current status, keyed by symbol."""
    try:
        state = _load_state(path)
    except Exception:  # noqa: BLE001 — _load_state already fails open
        state = {}
    out: dict[str, dict[str, Any]] = {}
    for sym, entry in (state.get("entries") or {}).items():
        status = cooldown_status(sym, on_date=on_date, path=path)
        out[sym] = {
            "exit_date": entry.get("exit_date"),
            "exit_price": entry.get("exit_price"),
            "shares": entry.get("shares"),
            "sessions": entry.get("sessions"),
            "recorded_at": entry.get("recorded_at"),
            **status,
        }
    return out
