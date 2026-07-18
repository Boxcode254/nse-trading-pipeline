"""Scanner — scheduled orchestrator for the market-intel layer.

Run on a cron schedule (every 4 hours per spec) to:

1. Fetch news for every tracked symbol.
2. Fetch the macro calendar.
3. Fetch earnings data for every tracked symbol.
4. Compute the sector rotation snapshot.
5. Write everything to a single ``context_store.json`` file that
   the advisor reads from.

The store is keyed by symbol (for news + earnings) and by date
(for calendar / sector). The file is the source of truth — if the
advisor can't reach the live sources at run time, it still has
the data the scanner wrote earlier.

Default location: ``~/.trading/data/context_store.json``.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import calendar, earnings, news, sector


_HOME = Path(os.path.expanduser("~/.trading"))


def _context_store_path() -> Path:
    return _HOME / "data" / "context_store.json"


def _tracked_symbols() -> list[str]:
    """Return the list of symbols to fetch news / earnings for.

    Reads from the project's central ``config.PAIRS`` so we don't
    duplicate the list. Forex pairs are filtered out — earnings
    don't apply to them, and news relevance is low.
    """
    try:
        from ... import config
        return [p for p in config.PAIRS if "/" not in p]
    except Exception:  # noqa: BLE001
        # Fallback hard-coded list — same as config.PAIRS, no forex.
        return ["SCOM", "KCB", "EQTY", "EABL", "ABSA", "SCBK"]


def run(*, store_path: Optional[Path] = None) -> dict:
    """Run a full scan and write the context store.

    Returns the store dict (also written to disk). Never raises —
    upstream failures are caught so a single broken source can't
    prevent the rest of the data from being saved.
    """
    store_path = store_path or _context_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)

    symbols = _tracked_symbols()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    store: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "news": {},
        "calendar": [],
        "earnings": {},
        "sectors": [],
    }

    # ── News ──────────────────────────────────────────────────────
    for sym in symbols:
        try:
            items = news.fetch([sym], use_cache=False, date=today)
        except Exception:  # noqa: BLE001
            items = []
        store["news"][sym] = items

    # ── Calendar ──────────────────────────────────────────────────
    try:
        store["calendar"] = calendar.upcoming()
    except Exception:  # noqa: BLE001
        store["calendar"] = []

    # ── Earnings ──────────────────────────────────────────────────
    try:
        store["earnings"] = earnings.upcoming(symbols)
    except Exception:  # noqa: BLE001
        store["earnings"] = {}

    # ── Sectors ───────────────────────────────────────────────────
    try:
        store["sectors"] = sector.snapshot()
    except Exception:  # noqa: BLE001
        store["sectors"] = []

    # ── Write atomically ──────────────────────────────────────────
    try:
        tmp = store_path.with_suffix(store_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, default=str)
        os.replace(tmp, store_path)
    except Exception:  # noqa: BLE001
        # Disk failure is non-fatal — the caller can still use the
        # in-memory store dict.
        pass

    return store


def load_store() -> dict:
    """Read the most recently written context store, or {} if absent."""
    path = _context_store_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def get_context_for_symbol(symbol: str) -> list[dict]:
    """Convenience reader: return assembled context for ``symbol``.

    Reads from the context store if present (fast path), or falls
    back to a live ``context.assemble`` call.
    """
    sym = symbol.upper()
    store = load_store()
    news_items = (store.get("news") or {}).get(sym, []) or []
    cal_events = store.get("calendar") or []
    earn_info = (store.get("earnings") or {}).get(sym)
    sector_snap = store.get("sectors") or []

    # Re-use the assembler for dedupe / ranking.
    from . import context as context_mod
    # Temporarily re-bind the upstream modules so the assembler uses
    # the store values without re-fetching.
    import contextlib
    from unittest.mock import patch
    with patch.object(context_mod, "news", new=_StubModule(fetch=lambda *a, **kw: news_items)), \
         patch.object(context_mod, "calendar", new=_StubModule(
             upcoming=lambda **kw: cal_events,
             _filter_relevant=context_mod._filter_relevant,
         )), \
         patch.object(context_mod, "earnings", new=_StubModule(
             upcoming=lambda *a, **kw: {sym: earn_info} if earn_info else {},
         )), \
         patch.object(context_mod, "sector", new=_StubModule(
             snapshot=lambda **kw: sector_snap,
             sector_for=sector.sector_for,
         )):
        return context_mod.assemble(sym, max_items=3)


class _StubModule:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
