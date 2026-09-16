"""Versioned ``eqw-v2`` equal-weight benchmark tracker — the Stage-1 gate evidence.

WHY THIS EXISTS
===============
The Stage-1 gate (2026-11-04) needs a same-date, net-of-cost comparison between
the paper book and an honest passive alternative. The legacy
``portfolio/benchmark.json`` (labelled ``eqw-v1-legacy`` by
:mod:`trading.services.benchmark_compare`) cannot supply it: its basket holds
forex pairs (``EUR/USD``/``USD/KES``) and the suspended ``BAMB``, it only
snapshots when some unrelated cron happens to fire, and it carries no version or
cost model. A verdict computed from it was published as a fake-precise
"-8.31pp" from two different dates over two different universes.

The pre-registered decision rule is fixed in
``audit/stage1-gate-preregistration-2026-09-15.md`` and BEFORE any data existed.
This module only produces the data the rule consumes; it never encodes the rule.

TWO VERSIONED BENCHMARKS (identical construction, different universe)
---------------------------------------------------------------------
``held``     equal-weight buy-and-hold of the positions held on the init date
             (the "what if I had just held my picks" test — the primary gate test).
``mandate``  equal-weight buy-and-hold of the tradable ``STRATEGY.stocks`` members
             (selection vs the opportunity set — reported as context, does not gate).

CONSTRUCTION
------------
* Init prices are captured ONCE, from the shared authority chain
  (:mod:`trading.price_source`: AXYS official close > feed > CSV), for the most
  recent session — never re-captured (a later re-init is a WINDOW RESET and, per
  the anti-goalpost clause, needs written approval + a re-accumulated window).
* A member whose price is unobtainable at init (e.g. BRIT has no ``nse_BRIT.csv``
  and no AXYS line) is EXCLUDED and the reason is recorded in the versioned
  record under ``excluded``. Never a hard failure, never silent.
* ``value(t) = initial_capital × mean(price_t / price_init) − entry_cost``
  where ``entry_cost = config.trade_cost(initial_capital, price=mean(init_prices))``
  (the same 1.5%/side + KES 60 floor + 0.15% slippage model the engine uses).
  The benchmark is a buy-and-hold: it pays the entry once and never sells.
* Tracking is idempotent per date: ``track()`` UPSERTS the day's row, so
  re-running the 15:30 job (or backfilling a date) can never double-count a
  session.

SCOPE / SAFETY
--------------
Reads go through :mod:`trading.price_source` only — no network, no scraping.
The only file this module writes is its own record
(``portfolio/benchmark_eqw_v2.json``). It never touches the legacy
``benchmark.json`` history (append-only, preserved), strategy weights, PAIRS,
STRATEGY targets or thresholds. Tests always pass a fixture directory.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .. import config as trading_config
from ..price_source import resolve_prices
from . import portfolio_status as pf_status

log = logging.getLogger("trading.benchmark_eqw_v2")

VERSION = "eqw-v2"
RECORD_FILENAME = "benchmark_eqw_v2.json"
INITIAL_CAPITAL = 100_000.0
HELD_KEY = "held"
MANDATE_KEY = "mandate"
BENCHMARK_KEYS = (HELD_KEY, MANDATE_KEY)

# Minimum comparable window (sessions) from the pre-registered rule. This is a
# REPORTING threshold, not a gate implementation: ``compare()`` surfaces
# ``window_below_minimum`` so the rule can be applied by a human/verdict layer.
MIN_WINDOW_SESSIONS = 20

# Reasons recorded on an exclusion (never silent).
EXCLUDED_NO_PRICE = "no_price_at_init"
EXCLUDED_NOT_TRADABLE = "not_tradable"

SNAPSHOTS_FILENAME = "snapshots.json"
MTM_FILENAME = pf_status.MTM_FILENAME
STATE_FILENAME = pf_status.STATE_FILENAME


class BenchmarkNotInitialized(RuntimeError):
    """``track()`` was called before ``init()`` wrote a record."""


# ── paths / io ────────────────────────────────────────────────────────────────
def portfolio_dir(dir_path: Optional[str] = None) -> Path:
    return pf_status.portfolio_dir(dir_path)


def record_path(dir_path: Optional[str] = None) -> Path:
    return portfolio_dir(dir_path) / RECORD_FILENAME


def load_record(dir_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Read the eqw-v2 record (read-only). None when absent/unreadable."""
    p = record_path(dir_path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("benchmarks"):
        return None
    return data


def write_record(record: dict[str, Any], dir_path: Optional[str] = None) -> Path:
    p = record_path(dir_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)
    return p


# ── small helpers ─────────────────────────────────────────────────────────────
def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _iso(value: Any) -> Optional[str]:
    d = _as_date(value)
    return d.isoformat() if d else None


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


# ── universes ─────────────────────────────────────────────────────────────────
def held_universe(dir_path: Optional[str] = None) -> list[str]:
    """Symbols the paper book holds as of the newest available stamp.

    Read from ``mtm_state.json`` (positions), falling back to ``state.json``.
    Order is the book's order, de-duplicated; missing state yields ``[]`` and
    ``init()`` then refuses (an empty "held" benchmark is meaningless).
    """
    base = portfolio_dir(dir_path)
    for name in (MTM_FILENAME, STATE_FILENAME):
        p = base / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        syms: list[str] = []
        for pos in data.get("positions") or []:
            sym = (pos or {}).get("symbol")
            if sym and sym not in syms:
                syms.append(str(sym))
        if syms:
            return syms
    return []


def mandate_universe() -> list[str]:
    """Tradable members of ``STRATEGY.stocks`` — the mandate opportunity set.

    Imported lazily (``target_allocation`` pulls the whole strategy stack) and
    filtered through the single tradability predicate (TP-003), so a suspended
    counter can never enter a benchmark universe. Deterministic (sorted).
    """
    from .. import tradability
    from ..target_allocation import STRATEGY

    members: list[str] = []
    for sector in (STRATEGY or {}).values():
        for sym in (sector or {}).get("stocks") or []:
            s = tradability.normalize_symbol(sym)
            if s and s not in members:
                members.append(s)
    return sorted(s for s in members if not tradability.is_suspended(s))


# ── cost model ────────────────────────────────────────────────────────────────
def entry_cost(capital: float, init_prices: dict[str, float]) -> float:
    """One-time entry cost of deploying ``capital`` equal-weight.

    Uses ``config.trade_cost`` (the engine's single cost model). The
    representative price is the equal-weight mean of the init prices: the
    slippage term is ``value × slippage_pct`` regardless of price, so this is
    the honest full cost of the initial deployment (fee + slippage), not the
    fee-only figure produced by ``trade_cost(value)`` with no price.
    """
    prices = [float(p) for p in init_prices.values() if p]
    rep = _mean(prices) or 0.0
    return float(trading_config.trade_cost(float(capital), rep).get("total") or 0.0)


def benchmark_value(
    init_prices: dict[str, float],
    prices: dict[str, float],
    universe: list[str],
    capital: float = INITIAL_CAPITAL,
    cost_kes: float = 0.0,
) -> Optional[float]:
    """``capital × mean(price_t / price_init) − cost_kes`` over ``universe``.

    None when the universe is empty or any member lacks an init price — a value
    over a silently shrunken basket is not a benchmark.
    """
    if not universe or capital <= 0:
        return None
    ratios: list[float] = []
    for sym in universe:
        px = prices.get(sym)
        px0 = init_prices.get(sym)
        if not px or not px0:
            return None
        ratios.append(float(px) / float(px0))
    mean_ratio = _mean(ratios)
    if mean_ratio is None:
        return None
    return round(float(capital) * mean_ratio - float(cost_kes), 2)


def return_pct(value: Optional[float], capital: float = INITIAL_CAPITAL) -> Optional[float]:
    if value is None or capital <= 0:
        return None
    return round((float(value) / float(capital) - 1.0) * 100.0, 4)


# ── init ──────────────────────────────────────────────────────────────────────
def _new_benchmark(universe: list[str], init_prices: dict[str, float],
                   excluded: list[dict[str, Any]], cost_kes: float) -> dict[str, Any]:
    return {
        "universe": list(universe),
        "init_prices": dict(init_prices),
        "excluded": list(excluded),
        "entry_cost_kes": round(float(cost_kes), 2),
        "snapshots": [],
    }


def init(
    dir_path: Optional[str] = None,
    as_of: Any = None,
    *,
    held: Optional[list[str]] = None,
    mandate: Optional[list[str]] = None,
    capital: float = INITIAL_CAPITAL,
    record: Optional[dict[str, Any]] = None,
    force: bool = False,
    write: bool = True,
) -> dict[str, Any]:
    """Capture init prices ONCE for both universes and write the versioned record.

    Returns ``{"record", "initialized", "written", "record_path", "excluded"}``.
    When a record already exists and ``force`` is False the existing record is
    returned untouched (``initialized=False``) — re-initialising is a WINDOW
    RESET, not a routine operation (see the anti-goalpost clause).
    """
    existing = record if record is not None else load_record(dir_path)
    if existing and not force:
        log.info("eqw-v2 record already initialised (init_date=%s); not re-capturing",
                 existing.get("init_date"))
        return {
            "record": existing,
            "initialized": False,
            "written": False,
            "record_path": str(record_path(dir_path)),
            "excluded": {k: (existing.get("benchmarks", {}).get(k) or {}).get("excluded", [])
                         for k in BENCHMARK_KEYS},
        }

    held_syms = list(held if held is not None else held_universe(dir_path))
    mandate_syms = list(mandate if mandate is not None else mandate_universe())
    wanted = {
        HELD_KEY: [s for s in held_syms if s],
        MANDATE_KEY: [s for s in mandate_syms if s],
    }
    missing_universe = [k for k, v in wanted.items() if not v]
    if missing_universe:
        raise ValueError(
            "empty benchmark universe for %s — refusing to initialise a benchmark "
            "over nothing" % ", ".join(missing_universe)
        )

    all_syms = sorted({s for v in wanted.values() for s in v})
    res = resolve_prices(all_syms, dir_path, as_of=as_of)
    # Capture date = the day the winning prices actually come from (the most
    # recent session), never "today" when only a stale close resolved.
    init_date = res.price_date or _iso(as_of) or _iso(datetime.now())
    if not res.prices:
        raise ValueError(
            "no init prices resolvable for %s (resolver=%s) — refusing to "
            "initialise" % (all_syms, res.summary())
        )

    benchmarks: dict[str, Any] = {}
    excluded_map: dict[str, list[dict[str, Any]]] = {}
    for key, syms in wanted.items():
        priced = [s for s in syms if res.prices.get(s)]
        excluded = [
            {
                "symbol": s,
                "reason": EXCLUDED_NO_PRICE,
                "detail": "%s has no price from the authority chain (AXYS>feed>CSV) "
                          "at init; excluded from the %s benchmark universe" % (s, key),
            }
            for s in syms
            if not res.prices.get(s)
        ]
        init_prices = {s: float(res.prices[s]) for s in priced}
        cost_kes = entry_cost(capital, init_prices)
        benchmarks[key] = _new_benchmark(priced, init_prices, excluded, cost_kes)
        excluded_map[key] = excluded
        for e in excluded:
            log.warning("eqw-v2 %s: excluded %s (%s)", key, e["symbol"], e["reason"])

    new_record: dict[str, Any] = {
        "version": VERSION,
        "init_date": init_date,
        "initial_capital": float(capital),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "resolver": res.provenance(),
        "benchmarks": benchmarks,
    }
    written = False
    if write:
        write_record(new_record, dir_path)
        written = True
    return {
        "record": new_record,
        "initialized": True,
        "written": written,
        "record_path": str(record_path(dir_path)),
        "excluded": excluded_map,
    }


# ── track ─────────────────────────────────────────────────────────────────────
def _upsert(snapshots: list[dict[str, Any]], row: dict[str, Any]) -> None:
    """Replace the row for ``row["date"]`` if present, else insert. Sorted."""
    day = row["date"]
    for i, s in enumerate(snapshots):
        if _iso(s.get("date")) == day:
            snapshots[i] = row
            break
    else:
        snapshots.append(row)
    snapshots.sort(key=lambda s: str(s.get("date") or ""))


def snapshot_for(record: dict[str, Any], key: str, on: Any) -> Optional[dict[str, Any]]:
    day = _iso(on)
    if day is None:
        return None
    for s in ((record.get("benchmarks") or {}).get(key) or {}).get("snapshots") or []:
        if _iso(s.get("date")) == day:
            return s
    return None


def track(
    on: Any,
    prices: Optional[dict[str, float]] = None,
    dir_path: Optional[str] = None,
    *,
    record: Optional[dict[str, Any]] = None,
    write: bool = True,
) -> dict[str, Any]:
    """Upsert one session's value for both benchmarks. Idempotent per date.

    ``prices`` may be injected (tests, backfill); otherwise they are resolved
    through the shared authority chain anchored at ``on``. A member that cannot
    be priced this session is reported in that benchmark's ``unpriced`` field and
    the row is NOT written — a value over a silently shrunken basket is not the
    benchmark. Never a hard failure, never silent.
    """
    day = _iso(on)
    if day is None:
        raise ValueError("track() requires a date, got %r" % (on,))
    rec = record if record is not None else load_record(dir_path)
    if not rec:
        raise BenchmarkNotInitialized(
            "no eqw-v2 record at %s — run init() first" % record_path(dir_path)
        )
    capital = float(rec.get("initial_capital") or INITIAL_CAPITAL)
    results: dict[str, Any] = {}
    for key in BENCHMARK_KEYS:
        bm = (rec.get("benchmarks") or {}).get(key) or {}
        universe = [str(s) for s in bm.get("universe") or []]
        init_prices = {s: float(p) for s, p in (bm.get("init_prices") or {}).items()}
        if prices is not None:
            px = {s: float(prices[s]) for s in universe if prices.get(s)}
            price_date = day
            source = "injected"
        else:
            res = resolve_prices(universe, dir_path, as_of=day)
            px = {s: float(v) for s, v in res.prices.items() if v}
            price_date = res.price_date
            source = "resolver"
        unpriced = [s for s in universe if not px.get(s)]
        value = benchmark_value(init_prices, px, universe, capital,
                               float(bm.get("entry_cost_kes") or 0.0))
        row = {
            "date": day,
            "value": value,
            "price_date": price_date,
            "unpriced": unpriced,
        }
        if value is not None:
            _upsert(bm.setdefault("snapshots", []), row)
        else:
            log.error("eqw-v2 %s: no value for %s (unpriced=%s)", key, day, unpriced)
        results[key] = {
            "value": value,
            "return_pct": return_pct(value, capital),
            "price_date": price_date,
            "price_source": source,
            "universe_size": len(universe),
            "unpriced": unpriced,
            "recorded": value is not None,
            "snapshot_count": len(bm.get("snapshots") or []),
        }
    written = False
    if write and any(r["recorded"] for r in results.values()):
        rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
        write_record(rec, dir_path)
        written = True
    return {"date": day, "results": results, "written": written, "record": rec}


def daily_track(
    dir_path: Optional[str] = None,
    as_of: Any = None,
    *,
    write: bool = True,
) -> dict[str, Any]:
    """One runtime step: init if the record is missing, then track ``as_of``.

    Tolerant by design — the 15:30 market-close job must never fail because of
    this tracker. Returns a payload describing what happened; the caller logs it.
    """
    rec = load_record(dir_path)
    payload: dict[str, Any] = {"init": None, "track": None}
    if not rec:
        try:
            payload["init"] = init(dir_path, as_of=as_of, write=write)
            rec = payload["init"]["record"]
        except (ValueError, OSError) as exc:
            payload["error"] = "init_failed: %s" % exc
            return payload
    payload["track"] = track(_iso(as_of) or datetime.now().date(), dir_path=dir_path,
                             record=rec, write=write)
    return payload


# ── window / rule inputs ──────────────────────────────────────────────────────
def window_sessions(record: dict[str, Any], key: str = HELD_KEY, through: Any = None) -> int:
    """Snapshot rows between the record's init date and ``through`` (inclusive).

    Every tracked row is one trading session by construction, so the row count
    is the comparable-window session count the pre-registered rule counts.
    """
    init_d = _as_date(record.get("init_date"))
    end_d = _as_date(through)
    rows = ((record.get("benchmarks") or {}).get(key) or {}).get("snapshots") or []
    n = 0
    for s in rows:
        d = _as_date(s.get("date"))
        if d is None:
            continue
        if init_d is not None and d < init_d:
            continue
        if end_d is not None and d > end_d:
            continue
        n += 1
    return n


def assess(
    record: Optional[dict[str, Any]],
    valuation_date: Any = None,
    *,
    min_sessions: int = MIN_WINDOW_SESSIONS,
) -> dict[str, Any]:
    """What the pre-registered rule needs, exposed as data (no rule logic).

    ``covers_valuation_date`` means BOTH benchmarks have a recorded row exactly
    at ``valuation_date`` — a same-date comparison is otherwise impossible.
    ``window_below_minimum`` is surfaced as a machine-readable reason when the
    comparable window is shorter than ``min_sessions``.
    """
    out: dict[str, Any] = {
        "available": bool(record),
        "version": (record or {}).get("version"),
        "init_date": (record or {}).get("init_date"),
        "initial_capital": (record or {}).get("initial_capital"),
        "valuation_date": _iso(valuation_date),
        "covers_valuation_date": False,
        "window_sessions": 0,
        "minimum_window_sessions": int(min_sessions),
        "below_minimum_window": None,
        "benchmarks": {},
        "reasons": [],
    }
    if not record:
        out["reasons"].append({"code": "eqw_v2_unavailable",
                               "detail": "no eqw-v2 benchmark record"})
        return out

    if str(out["version"] or "") != VERSION:
        out["reasons"].append({
            "code": "eqw_v2_version_mismatch",
            "detail": "record version %r is not %r" % (out["version"], VERSION),
        })
        return out

    covers = True
    for key in BENCHMARK_KEYS:
        bm = (record.get("benchmarks") or {}).get(key) or {}
        snap = snapshot_for(record, key, valuation_date) if valuation_date else \
            ((bm.get("snapshots") or [])[-1] if bm.get("snapshots") else None)
        value = snap.get("value") if snap else None
        covers = covers and value is not None
        out["benchmarks"][key] = {
            "universe": list(bm.get("universe") or []),
            "universe_size": len(bm.get("universe") or []),
            "excluded": list(bm.get("excluded") or []),
            "entry_cost_kes": bm.get("entry_cost_kes"),
            "snapshot_date": _iso(snap.get("date")) if snap else None,
            "value": value,
            "return_pct": return_pct(value, float(record.get("initial_capital") or INITIAL_CAPITAL)),
            "window_sessions": window_sessions(record, key, through=valuation_date),
            "unpriced": list(snap.get("unpriced") or []) if snap else [],
        }
    out["covers_valuation_date"] = bool(covers and valuation_date is not None)
    # The window is a property of the record's tracking (identical for both
    # universes by construction); report the held window as the gate window.
    out["window_sessions"] = int(
        (out["benchmarks"].get(HELD_KEY) or {}).get("window_sessions") or 0
    )
    out["below_minimum_window"] = out["window_sessions"] < int(min_sessions)

    if not out["covers_valuation_date"]:
        out["reasons"].append({
            "code": "eqw_v2_no_snapshot_at_valuation_date",
            "detail": "no eqw-v2 row for %s (last %s)" % (
                out["valuation_date"],
                (out["benchmarks"].get(HELD_KEY) or {}).get("snapshot_date"),
            ),
            "valuation_date": out["valuation_date"],
        })
    if out["below_minimum_window"]:
        out["reasons"].append({
            "code": "window_below_minimum",
            "detail": "window_below_minimum(sessions=%d, minimum=%d)"
                      % (out["window_sessions"], int(min_sessions)),
            "window_sessions": out["window_sessions"],
            "minimum_window_sessions": int(min_sessions),
        })
    return out


def strategy_max_drawdown(
    dir_path: Optional[str] = None,
    start: Any = None,
    end: Any = None,
    *,
    snapshots: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Peak-to-trough drawdown of the strategy book over [start, end].

    Series: ``portfolio/snapshots.json`` (the canonical equity curve). Returns
    ``max_drawdown_pct`` plus provenance, or an explicit reason when the series
    has too little data in the window — never a fabricated 0.0.
    """
    rows = snapshots
    if rows is None:
        p = portfolio_dir(dir_path) / SNAPSHOTS_FILENAME
        if not p.exists():
            return {"max_drawdown_pct": None, "points": 0, "source": str(p),
                    "reason": "snapshots_unavailable"}
        try:
            rows = json.loads(p.read_text())
        except (OSError, ValueError):
            return {"max_drawdown_pct": None, "points": 0, "source": str(p),
                    "reason": "snapshots_unreadable"}
    s_d, e_d = _as_date(start), _as_date(end)
    points: list[tuple[date, float]] = []
    for r in rows or []:
        d = _as_date((r or {}).get("timestamp") or (r or {}).get("date"))
        v = (r or {}).get("total_value")
        if d is None or v is None:
            continue
        if s_d is not None and d < s_d:
            continue
        if e_d is not None and d > e_d:
            continue
        points.append((d, float(v)))
    if len(points) < 2:
        return {"max_drawdown_pct": None, "points": len(points),
                "source": SNAPSHOTS_FILENAME, "reason": "insufficient_snapshots_in_window"}
    points.sort(key=lambda t: t[0])
    peak = points[0][1]
    worst = 0.0
    for _, v in points:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100.0)
    return {
        "max_drawdown_pct": round(worst, 4),
        "points": len(points),
        "start": points[0][0].isoformat(),
        "end": points[-1][0].isoformat(),
        "source": SNAPSHOTS_FILENAME,
    }


def format_record_summary(record: dict[str, Any]) -> list[str]:
    """Plain-text lines for the record (no Rich/Telegram markup)."""
    lines = [
        "eqw-v2 benchmark record",
        "  version:      %s" % record.get("version"),
        "  init date:    %s" % record.get("init_date"),
        "  capital:      KES %s" % record.get("initial_capital"),
    ]
    for key in BENCHMARK_KEYS:
        bm = (record.get("benchmarks") or {}).get(key) or {}
        snaps = bm.get("snapshots") or []
        last = snaps[-1] if snaps else {}
        lines.append(
            "  %-8s %d symbols, %d sessions, last %s = %s"
            % (key, len(bm.get("universe") or []), len(snaps),
               last.get("date"), last.get("value"))
        )
        for e in bm.get("excluded") or []:
            lines.append("    excluded: %s (%s)" % (e.get("symbol"), e.get("reason")))
    return lines


# ── CLI (runtime entry point for the 15:30 market-close refresh) ───────────────
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="eqw-v2 versioned benchmark tracker")
    ap.add_argument("command", choices=["init", "track", "daily", "show"])
    ap.add_argument("--date", default=None,
                    help="session date (YYYY-MM-DD); default = today")
    ap.add_argument("--dir", default=None, help="portfolio dir override (fixtures)")
    ap.add_argument("--force", action="store_true",
                    help="init only: re-capture (WINDOW RESET — needs approval)")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    day = args.date or datetime.now().date().isoformat()

    if args.command == "init":
        payload = init(args.dir, as_of=day, force=args.force)
    elif args.command == "track":
        payload = track(day, dir_path=args.dir)
    elif args.command == "daily":
        payload = daily_track(args.dir, as_of=day)
    else:  # show
        rec = load_record(args.dir)
        if not rec:
            print("no eqw-v2 record at %s" % record_path(args.dir))
            return 0
        print("\n".join(format_record_summary(rec)))
        return 0

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        rec = payload.get("record") or load_record(args.dir)
        if rec:
            print("\n".join(format_record_summary(rec)))
        if payload.get("error"):
            print("ERROR: %s" % payload["error"])
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the smoke test
    sys.exit(main())
