"""Single source of truth for position pricing.

WHY THIS MODULE EXISTS
----------------------
``portfolio/state.json`` is the file the auto-trader's sizing/allocation logic
and the dashboard actually read. Historically its per-position ``current_value``
was a *cached write*: whichever cron happened to run last stamped a price into
it. That produced two proven failure modes:

  SEQ-A  cron-ordering race
         The intraday feed writes a price at 15:30 (``refresh-mtm.py``). The
         OFFICIAL NSE close only lands later, when ``axys_reconcile.py`` runs
         (19:30 / 21:00 fallback) -- and that script writes ``mtm_state.json``
         ONLY; ``state.json`` is not one of its targets. Nothing re-writes
         ``state.json`` afterwards, so the book stays stuck on the feed price
         until some unrelated cron (06:00 / 15:30) happens to fire. A 2.78%
         book error on EABL was reproduced this way.

  SEQ-B  trade wipes market prices
         ``engine.Position.to_dict()`` hardcodes ``current_value = total_cost``,
         so ANY ``_save_state()`` -- i.e. any paper trade -- reset every
         position's market value to COST BASIS. Reproduced: 10/11 positions
         destroyed by a single save.

The fix is to stop treating price as a stored fact and resolve it *live* from a
ranked authority chain at both chokepoints (write and read).

AUTHORITY CHAIN (highest first)
-------------------------------
  1. ``axys``  AXYS Daily Market Watch official NSE close
               (``portfolio/axys_closes_<date>.json``). This is the NSE tape.
  2. ``feed``  ``mtm_state.json`` ``live_price`` -- the intraday feed. Correct
               and expected during market hours, when no official close for
               "today" exists yet.
  3. ``csv``   ``data/nse_<SYM>.csv`` last close -- last-resort offline cache.
  4. ``None``  caller falls back to cost basis (suspended / uncovered names).

WINDOW POLICY (matches axys_reconcile.py / book_integrity_check.py /
portfolio_mtm.py so all four agree):
  * AXYS_SEARCH_WINDOW_DAYS = 7 -- how far back we will look for a usable
    official-close file (tolerates weekends + a public holiday).
  * STALE_MAX_DAYS = 3 -- the freshness standard for *alerting*. Decoupled from
    the search window on purpose: we still price off a 5-day-old official close
    rather than silently degrading to feed, but the staleness is reported.

SCOPE: this module resolves PRICE ONLY. Share counts, cash, cost basis and the
transaction ledger are owned by the execution engine and are never touched here.
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

# Kept identical to portfolio_mtm.py / book_integrity_check.py on purpose.
AXYS_SEARCH_WINDOW_DAYS = 7
STALE_MAX_DAYS = 3


def _portfolio_dir(dir_path: Optional[str] = None) -> Path:
    if dir_path:
        return Path(dir_path)
    return Path(os.path.expanduser("~/.trading/portfolio"))


def axys_file_date(filename: str) -> Optional[str]:
    """Extract the ISO date from 'axys_closes_YYYY-MM-DD.json', else None.

    Shared helper -- this logic previously existed in two places
    (book_integrity_check.py and portfolio_mtm.py) and drifted.
    """
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename or "")
    return m.group(1) if m else None


def _coerce_date(value: Any) -> Optional[datetime.date]:
    """Accept a date, a datetime or an ISO-ish string; return a date."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def load_axys_closes(
    dir_path: Optional[str] = None,
    as_of: Any = None,
) -> tuple[dict[str, float], Optional[str], int]:
    """Return (closes, source_filename, age_days) for the newest usable file.

    Walks back up to AXYS_SEARCH_WINDOW_DAYS looking for an
    ``axys_closes_<date>.json`` that actually carries prices. Returns
    ``({}, None, -1)`` when nothing usable is in the window.

    ``as_of`` (date | datetime | "YYYY-MM-DD") anchors the walk-back so a
    comparison can be reproduced for a historical as-of date instead of
    silently pricing everything off *today*. Default None == today, which
    preserves every existing caller's behaviour.
    """
    base = _portfolio_dir(dir_path)
    today = _coerce_date(as_of) or datetime.date.today()
    for back in range(0, AXYS_SEARCH_WINDOW_DAYS + 1):
        d = (today - datetime.timedelta(days=back)).isoformat()
        p = base / f"axys_closes_{d}.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        closes = {
            k: float(v)
            for k, v in (data.get("axys") or {}).items()
            if v
        }
        if closes:
            return closes, p.name, back
    return {}, None, -1


def _load_feed_prices(
    dir_path: Optional[str] = None,
    as_of: Any = None,
) -> tuple[dict[str, float], Optional[str]]:
    """live_price per symbol from mtm_state.json (the intraday feed).

    Returns ``(prices, stamp_date)``. The feed is a *live* stamp: it is only
    a valid as-of price when the requested as-of date is the day the stamp
    was written. A historical as-of therefore gets ``({}, stamp_date)`` so the
    caller falls through to the official close instead of borrowing today's
    tape for a past date.
    """
    p = _portfolio_dir(dir_path) / "mtm_state.json"
    if not p.exists():
        return {}, None
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}, None
    stamp = (data.get("generated_at") or "")[:10] or None
    want = _coerce_date(as_of)
    if want is not None and stamp and want.isoformat() != stamp:
        return {}, stamp
    out: dict[str, float] = {}
    for pos in data.get("positions", []):
        sym = pos.get("symbol")
        lp = pos.get("live_price")
        if sym and lp:
            try:
                out[sym] = float(lp)
            except (TypeError, ValueError):
                continue
    return out, stamp


def _csv_close(
    symbol: str,
    dir_path: Optional[str] = None,
) -> Optional[float]:
    """Last close from data/nse_<SYM>.csv -- last-resort offline cache."""
    data_dir = _portfolio_dir(dir_path).parent / "data" if dir_path else Path(
        os.path.expanduser("~/.trading/data")
    )
    p = data_dir / f"nse_{symbol}.csv"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return None
        last = rows[-1]
        close = last.get("close") or last.get("Close")
        return float(close) if close else None
    except (OSError, ValueError, KeyError):
        return None


class PriceResolution:
    """Resolved prices plus the provenance needed to audit them."""

    def __init__(
        self,
        prices: dict[str, float],
        sources: dict[str, str],
        axys_file: Optional[str],
        axys_age_days: int,
        as_of: Optional[str] = None,
        price_date: Optional[str] = None,
    ) -> None:
        self.prices = prices
        self.sources = sources
        self.axys_file = axys_file
        self.axys_age_days = axys_age_days
        # ISO date the resolution was requested for (None == "now").
        self.as_of = as_of
        # ISO date the winning prices actually come from (official close file
        # date, or the feed stamp date). None when nothing resolved.
        self.price_date = price_date

    @property
    def axys_stale(self) -> bool:
        """True when the official close backing us is past the alert standard."""
        return self.axys_age_days < 0 or self.axys_age_days > STALE_MAX_DAYS

    def provenance(self) -> dict[str, Any]:
        """Machine-readable provenance — the audit trail for a valuation."""
        return {
            "as_of": self.as_of,
            "price_date": self.price_date,
            "official_close_file": self.axys_file,
            "official_close_age_days": self.axys_age_days,
            "stale": self.axys_stale,
            "sources": dict(self.sources),
        }

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for s in self.sources.values():
            counts[s] = counts.get(s, 0) + 1
        parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        src = self.axys_file or "none"
        return (
            f"official={src} age={self.axys_age_days}d "
            f"stale={self.axys_stale} [{parts}]"
        )


def resolve_prices(
    symbols: list[str],
    dir_path: Optional[str] = None,
    as_of: Any = None,
) -> PriceResolution:
    """Resolve each symbol to its most authoritative available price.

    AXYS official close > mtm_state feed > CSV cache > unresolved.

    ``as_of`` anchors the resolution to a date (default: now). Prices are
    never borrowed from a *different* date: the feed only counts when its
    stamp matches the requested date, and the official-close walk-back starts
    at ``as_of`` rather than today.
    """
    want = _coerce_date(as_of)
    want_iso = want.isoformat() if want else None
    closes, axys_file, age = load_axys_closes(dir_path, as_of=want)
    feed, feed_stamp = _load_feed_prices(dir_path, as_of=want)

    prices: dict[str, float] = {}
    sources: dict[str, str] = {}
    price_date: Optional[str] = None
    for sym in symbols:
        if sym in closes and closes[sym] > 0:
            prices[sym] = closes[sym]
            sources[sym] = "axys"
            if price_date is None:
                price_date = axys_file_date(axys_file) if axys_file else None
            continue
        if sym in feed and feed[sym] > 0:
            prices[sym] = feed[sym]
            sources[sym] = "feed"
            if price_date is None:
                price_date = feed_stamp
            continue
        c = _csv_close(sym, dir_path)
        if c and c > 0:
            prices[sym] = c
            sources[sym] = "csv"
    return PriceResolution(
        prices, sources, axys_file, age, as_of=want_iso, price_date=price_date
    )


def apply_authoritative_prices(
    state: dict[str, Any],
    dir_path: Optional[str] = None,
    previous: Optional[dict[str, Any]] = None,
) -> PriceResolution:
    """Overwrite each position's ``current_value`` with the live-resolved price.

    Mutates ``state`` in place. PRICE FIELD ONLY -- ``shares``, ``avg_cost``,
    ``total_cost``, ``cash`` and everything else are left exactly as passed in.

    Fallback order per position when the resolver has no price (suspended or
    uncovered name):
      1. the value already on disk (``previous``), which is a real historical
         mark and strictly better than cost basis;
      2. cost basis, matching the engine's long-standing behaviour.
    """
    positions = state.get("positions") or []
    syms = [p.get("symbol") for p in positions if p.get("symbol")]
    res = resolve_prices(syms, dir_path)

    prev_by_sym: dict[str, float] = {}
    for p in (previous or {}).get("positions", []) or []:
        s = p.get("symbol")
        cv = p.get("current_value")
        if s and cv:
            try:
                prev_by_sym[s] = float(cv)
            except (TypeError, ValueError):
                continue

    for p in positions:
        sym = p.get("symbol")
        shares = p.get("shares") or 0
        if not sym or not shares:
            continue
        px = res.prices.get(sym)
        if px:
            p["current_value"] = round(shares * px, 2)
        elif sym in prev_by_sym:
            p["current_value"] = round(prev_by_sym[sym], 2)
        else:
            p["current_value"] = round(float(p.get("total_cost") or 0.0), 2)
    return res
