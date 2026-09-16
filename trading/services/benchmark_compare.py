"""Same-date, same-universe portfolio-vs-benchmark comparison — read-only.

WHY THIS EXISTS
===============
The daily supervision report used to publish a precise Stage-1 gate verdict
from two numbers that were not comparable at all:

  * the portfolio value came from the 2026-09-14 mark-to-market stamp;
  * the benchmark value came from the last ``benchmark.json`` snapshot, taken
    2026-09-09 — five days earlier and from a different universe (the legacy
    benchmark basket includes FX pairs ``EUR/USD``/``USD/KES`` and the
    suspended ``BAMB``, while the live book is nine NSE equities);
  * the staleness caveat in the message was a hardcoded string ("2d stale").

The result was a fake-precise "gross gap -5.31pp / net -8.31pp".

This module replaces that with one comparison that either establishes
comparability explicitly or REFUSES to produce a gap:

  1. ``as_of`` is derived from the canonical portfolio read model
     (:mod:`trading.services.portfolio_status`) and is the SAME timestamp
     used for the benchmark side.
  2. Both sides are priced through the SAME authority chain
     (:mod:`trading.price_source`: AXYS official close > feed > CSV), anchored
     at that as-of date. Prices are never borrowed across dates.
  3. The eligible universe is EXPLICIT (``eligible_universe``) and the
     benchmark's declared universe is versioned
     (``benchmark_universe_version``). A universe mismatch is a blocker, not a
     footnote.
  4. The cash and cost treatment is declared once in ``cost_policy`` /
     ``cash_policy`` and applied identically to both sides.
  5. If any of that cannot be established the verdict is blocked:
     ``comparison_status == "incomparable"`` with machine-readable reasons, and
     ``gap_gross_pct`` / ``gap_net_pct`` are ``None``. A precise gap is only
     ever published when the comparison is genuinely same-date and
     same-universe.

READ-ONLY: nothing here writes ``portfolio/*.json``, snapshots or the
benchmark history. Historical benchmark records are read and reported, never
rewritten — the benchmark value can be recomputed on the fly for a diagnostic
cross-check, and that recomputation is explicitly marked as not used for the
verdict.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from ..price_source import resolve_prices
from . import benchmark_eqw_v2 as eqw_v2
from . import portfolio_status as pf_status

# Stage-1 gate configuration (NOT changed by TP-005 — reported, not redefined).
GATE_DEADLINE = "2026-11-04"
MAX_DRAWDOWN_HALT_PCT = 15.0

# The benchmark basket recorded in portfolio/benchmark.json predates this
# module and carries no version field, so its universe is labelled explicitly
# rather than silently treated as canonical. New benchmark definitions are
# expected to carry their own ``universe_version`` key.
BENCHMARK_UNIVERSE_VERSION_LEGACY = "eqw-v1-legacy"

# Declared cost policy, applied identically to both sides. The strategy pays one
# round trip (entry + exit at the gate); the benchmark is a buy-and-hold and
# never sells, so its declared round-trip cost is zero.
DEFAULT_COST_POLICY: dict[str, Any] = {
    "strategy_round_trip_cost_pct": 3.0,
    "benchmark_round_trip_cost_pct": 0.0,
    "gate_basis": "strategy net of its own round-trip costs vs benchmark hold gross",
    "declared": True,
}

# A book or a benchmark record older than this cannot be compared without
# pretending the missing days did not happen.
DEFAULT_MAX_AGE_DAYS = 3

# When the comparison is sourced from the versioned eqw-v2 record, BOTH sides
# are already net of their modelled costs: the paper book through the engine's
# trade-cost model, the benchmark through the entry cost recorded in the
# eqw-v2 record. No further round-trip charge is applied, so the published gap
# is directly the pre-registered rule's "strategy net return vs benchmark net
# return" (audit/stage1-gate-preregistration-2026-09-15.md).
V2_COST_POLICY: dict[str, Any] = {
    "strategy_round_trip_cost_pct": 0.0,
    "benchmark_round_trip_cost_pct": 0.0,
    "gate_basis": (
        "both sides net — canonical book (engine trade-cost model applied) vs "
        "eqw-v2 (recorded one-time entry cost applied)"
    ),
    "declared": True,
    "benchmark_source": eqw_v2.VERSION,
}

# Reason codes that describe the LEGACY benchmark record's fitness rather than
# the comparability of the valuation. With a covered eqw-v2 record driving the
# verdict these are demoted to the ``legacy_benchmark`` block (reported for
# continuity, never silently dropped) instead of blocking the gate.
LEGACY_BENCHMARK_REASON_CODES = frozenset({
    "benchmark_unavailable",
    "benchmark_has_no_snapshots",
    "benchmark_record_stale",
    "benchmark_snapshot_value_unreadable",
})


def benchmark_path(dir_path: Optional[str] = None) -> Path:
    return pf_status.portfolio_dir(dir_path) / "benchmark.json"


def load_benchmark(dir_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Read portfolio/benchmark.json (read-only). None when absent/unreadable."""
    p = benchmark_path(dir_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


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


def _last_snapshot(benchmark: dict[str, Any]) -> Optional[dict[str, Any]]:
    snaps = benchmark.get("snapshots") or []
    return snaps[-1] if snaps else None


def _benchmark_universe(benchmark: dict[str, Any]) -> list[str]:
    assets = benchmark.get("assets") or []
    return [str(a) for a in assets]


def _benchmark_universe_version(benchmark: dict[str, Any]) -> str:
    return str(
        benchmark.get("universe_version") or BENCHMARK_UNIVERSE_VERSION_LEGACY
    )


def _reason(code: str, **fields: Any) -> dict[str, Any]:
    detail = ", ".join(f"{k}={v}" for k, v in fields.items())
    return {"code": code, "detail": f"{code}({detail})" if detail else code, **fields}


def stage1_rule_block(
    *,
    strategy_return_pct: Optional[float],
    benchmark_return_pct: Optional[float],
    gap_net_pct: Optional[float],
    window_sessions: int,
    minimum_window_sessions: int = eqw_v2.MIN_WINDOW_SESSIONS,
    drawdown_pct: Optional[float] = None,
    mandate_return_pct: Optional[float] = None,
) -> dict[str, Any]:
    """Expose each pre-registered rule clause with its inputs and PASS/FAIL.

    DATA ONLY — the rule itself lives in
    ``audit/stage1-gate-preregistration-2026-09-15.md`` and is evaluated there
    by a human/verdict layer. A verdict that cannot show its inputs is invalid,
    so everything the rule needs is exposed here: window session count, strategy
    net return, both benchmark net returns, max drawdown, and per-clause results.
    """
    window_ok = window_sessions >= minimum_window_sessions

    def clause(ok: Optional[bool], **fields: Any) -> dict[str, Any]:
        return {"pass": ok, **fields}

    clauses = {
        "window_minimum_met": clause(
            window_ok,
            window_sessions=window_sessions,
            minimum_window_sessions=minimum_window_sessions,
        ),
        "strategy_net_return_ge_zero": clause(
            None if strategy_return_pct is None else strategy_return_pct >= 0.0,
            value_pct=strategy_return_pct,
        ),
        "strategy_beats_eqw_v2_held": clause(
            None if gap_net_pct is None else gap_net_pct >= 0.0,
            gap_net_pct=gap_net_pct,
            strategy_return_pct=strategy_return_pct,
            benchmark_return_pct=benchmark_return_pct,
        ),
        "max_drawdown_below_limit": clause(
            None if drawdown_pct is None else drawdown_pct < MAX_DRAWDOWN_HALT_PCT,
            value_pct=drawdown_pct,
            limit_pct=MAX_DRAWDOWN_HALT_PCT,
        ),
    }
    values = [c["pass"] for c in clauses.values()]
    if not window_ok:
        outcome = "INSUFFICIENT_WINDOW"
    elif all(v is True for v in values):
        outcome = "GO"
    elif any(v is False for v in values):
        outcome = "NO-GO"
    else:
        outcome = "UNRESOLVED"
    return {
        "rule_source": "audit/stage1-gate-preregistration-2026-09-15.md",
        "benchmark_used": "%s-%s" % (eqw_v2.VERSION, eqw_v2.HELD_KEY),
        "mandate_role": "context only (does not gate)",
        "window_sessions": window_sessions,
        "minimum_window_sessions": minimum_window_sessions,
        "strategy_net_return_pct": strategy_return_pct,
        "benchmark_net_return_pct": benchmark_return_pct,
        "mandate_net_return_pct": mandate_return_pct,
        "max_drawdown_pct": drawdown_pct,
        "clauses": clauses,
        "outcome_if_evaluated_now": outcome,
        "note": "inputs exposed for the pre-registered rule; the verdict is not "
                "implemented in code",
    }


def recompute_equal_weight_benchmark(
    benchmark: dict[str, Any],
    universe: list[str],
    dir_path: Optional[str] = None,
    as_of: Any = None,
    resolution: Optional[Any] = None,
) -> dict[str, Any]:
    """Value the declared equal-weight hold at ``as_of`` with the shared resolver.

    Returns ``{"value", "return_pct", "initial_capital", "prices", "price_date",
    "missing_prices", "missing_init_prices"}``. This is a same-date,
    same-resolver recomputation of the benchmark from its own recorded init
    prices; it is a diagnostic cross-check and, when the recorded snapshot for
    the as-of date exists, never overrides it.
    """
    init = benchmark.get("init_prices") or {}
    capital = float(benchmark.get("initial_capital") or 0.0)
    missing_init = [s for s in universe if not init.get(s)]
    res = resolution if resolution is not None else resolve_prices(
        universe, dir_path, as_of=as_of
    )
    missing_px = [s for s in universe if not res.prices.get(s)]
    out: dict[str, Any] = {
        "value": None,
        "return_pct": None,
        "initial_capital": capital,
        "prices": {s: res.prices.get(s) for s in universe},
        "price_date": res.price_date,
        "missing_prices": missing_px,
        "missing_init_prices": missing_init,
    }
    if missing_init or missing_px or not universe or capital <= 0:
        return out
    returns = [
        (float(res.prices[s]) / float(init[s]) - 1.0) for s in universe
    ]
    mean_ret = sum(returns) / len(returns)
    out["value"] = round(capital * (1.0 + mean_ret), 2)
    out["return_pct"] = round(mean_ret * 100.0, 4)
    return out


def compare(
    dir_path: Optional[str] = None,
    portfolio_status: Optional[dict[str, Any]] = None,
    benchmark: Optional[dict[str, Any]] = None,
    eligible_universe: Optional[list[str]] = None,
    as_of: Any = None,
    cost_policy: Optional[dict[str, Any]] = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    benchmark_v2: Optional[dict[str, Any]] = None,
    snapshots: Optional[list[dict[str, Any]]] = None,
    min_window_sessions: int = eqw_v2.MIN_WINDOW_SESSIONS,
) -> dict[str, Any]:
    """Compare the canonical book against the benchmark, or refuse to.

    ``comparison_status`` is ``"comparable"`` only when the portfolio and the
    benchmark are valued as of the same date, over the same explicitly declared
    universe, through the same price resolver, with the same declared
    cash/cost policy. Otherwise it is ``"incomparable"``, ``reasons`` carries
    why, and the gap fields are ``None``.

    Benchmark side (Task 6): when the versioned
    ``portfolio/benchmark_eqw_v2.json`` record exists AND has a snapshot at the
    valuation date, IT is the benchmark for the verdict
    (``benchmark_source == "eqw-v2"``) and the legacy record's own defects are
    demoted into ``legacy_benchmark`` instead of blocking. A v2 window shorter
    than ``min_window_sessions`` (default 20, the pre-registered minimum)
    surfaces as the machine-readable reason ``window_below_minimum``; the rule
    itself is NOT implemented here. ``stage1_rule`` exposes each clause's input
    and PASS/FAIL so a verdict can show its work.

    Injection points (``portfolio_status`` / ``benchmark`` / ``benchmark_v2`` /
    ``eligible_universe`` / ``snapshots`` / ``as_of``) exist so tests can use
    deterministic fixtures without touching runtime state.
    """
    cost = dict(DEFAULT_COST_POLICY)
    if cost_policy:
        cost.update(cost_policy)
    pf = portfolio_status if portfolio_status is not None else pf_status.current_status(dir_path)
    bm = benchmark if benchmark is not None else load_benchmark(dir_path)

    reasons: list[dict[str, Any]] = []
    universe_mismatch = False
    benchmark_age_days: Optional[int] = None

    # ── portfolio side ────────────────────────────────────────────────────
    pf_value = pf.get("portfolio_value")
    if pf_value is None:
        reasons.append(_reason("portfolio_state_unavailable", source=pf.get("source")))
    pf_symbols = list(
        eligible_universe
        if eligible_universe is not None
        else (pf.get("eligible_universe") or [p.get("symbol") for p in pf.get("positions") or []])
    )
    pf_symbols = [s for s in pf_symbols if s]
    if eligible_universe is not None and not eligible_universe:
        reasons.append(_reason("empty_eligible_universe"))

    as_of_date = _as_date(as_of) or _as_date(pf.get("as_of_date")) or _as_date(pf.get("as_of"))
    if as_of_date is None:
        reasons.append(_reason("as_of_unknown", portfolio_as_of=pf.get("as_of")))

    pf_age = pf.get("age_days")
    if isinstance(pf_age, (int, float)) and pf_age > max_age_days:
        reasons.append(_reason("portfolio_stale", portfolio_age_days=round(float(pf_age), 2)))

    # ── benchmark side ────────────────────────────────────────────────────
    if not bm:
        reasons.append(_reason("benchmark_unavailable", path=str(benchmark_path(dir_path))))
        bm_last = None
        bm_universe: list[str] = []
        bm_version = BENCHMARK_UNIVERSE_VERSION_LEGACY
    else:
        bm_universe = _benchmark_universe(bm)
        bm_version = _benchmark_universe_version(bm)
        bm_last = _last_snapshot(bm)
        if bm_last is None:
            reasons.append(_reason("benchmark_has_no_snapshots"))

    bm_last_date = _as_date(bm_last.get("timestamp")) if bm_last else None
    bm_last_value: Optional[float] = None
    if bm_last is not None:
        try:
            bm_last_value = float(bm_last.get("value"))
        except (TypeError, ValueError):
            bm_last_value = None
            reasons.append(_reason("benchmark_snapshot_value_unreadable"))

    if as_of_date is not None and bm_last_date is not None:
        delta = (as_of_date - bm_last_date).days
        benchmark_age_days = delta
        if delta != 0:
            reasons.append(
                _reason(
                    "benchmark_record_stale",
                    benchmark_age_days=delta,
                    benchmark_snapshot_date=bm_last_date.isoformat(),
                    portfolio_as_of_date=as_of_date.isoformat(),
                )
            )

    # ── universe comparability ────────────────────────────────────────────
    if bm and pf_symbols:
        extra = sorted(set(bm_universe) - set(pf_symbols))
        missing = sorted(set(pf_symbols) - set(bm_universe))
        if extra or missing:
            universe_mismatch = True
            reasons.append(
                _reason(
                    "universe_mismatch",
                    benchmark_universe_version=bm_version,
                    not_in_portfolio=extra,
                    not_in_benchmark=missing,
                    benchmark_size=len(bm_universe),
                    portfolio_size=len(pf_symbols),
                )
            )

    # ── eqw-v2 versioned benchmark (Task 6) ───────────────────────────────
    # The versioned record is the pre-registered rule's benchmark. It is used
    # for the verdict when it EXISTS and COVERS the valuation date; the legacy
    # record is still read, reported and (when nothing else is available) used,
    # so its append-only history keeps its continuity role.
    v2_record = (
        benchmark_v2 if benchmark_v2 is not None else eqw_v2.load_record(dir_path)
    )
    v2 = (
        eqw_v2.assess(v2_record, as_of_date, min_sessions=min_window_sessions)
        if v2_record
        else None
    )
    v2_benchmarks = (v2 or {}).get("benchmarks") or {}
    v2_held = v2_benchmarks.get(eqw_v2.HELD_KEY) or {}
    v2_mandate = v2_benchmarks.get(eqw_v2.MANDATE_KEY) or {}
    use_v2 = bool(v2 and v2.get("covers_valuation_date"))
    v2_universe = sorted(
        set(v2_held.get("universe") or []) | set(v2_mandate.get("universe") or [])
    )

    # ── same resolver, anchored at the same as-of date ────────────────────
    recompute_universe = sorted(set(bm_universe) | set(pf_symbols) | set(v2_universe))
    if not recompute_universe:
        recompute_universe = list(pf_symbols)
    res = resolve_prices(recompute_universe, dir_path, as_of=as_of_date)
    recomputed = (
        recompute_equal_weight_benchmark(
            bm, recompute_universe, dir_path, as_of=as_of_date, resolution=res
        )
        if bm
        else None
    )

    required = sorted(set(pf_symbols) | set(bm_universe) | set(v2_universe))
    unpriced = [s for s in required if not res.prices.get(s)]
    if unpriced:
        reasons.append(_reason("unpriced_symbols", symbols=unpriced))

    # ── benchmark sourcing + reason routing (eqw-v2 vs legacy) ────────────
    # A covered eqw-v2 record drives the verdict. The legacy record's own
    # defects (stale snapshot, legacy-basket universe mismatch) are about the
    # OLD basket, so they are then demoted into ``legacy_benchmark`` — reported,
    # never silently dropped — instead of blocking a comparison that no longer
    # depends on them. Portfolio-side and resolver-side reasons always block.
    legacy_reasons: list[dict[str, Any]] = []
    if use_v2:
        legacy_reasons = [
            r for r in reasons
            if r["code"] in LEGACY_BENCHMARK_REASON_CODES
            or r["code"] == "unpriced_symbols"   # re-derived below over the live/v2 sets
            or (
                r["code"] == "universe_mismatch"
                and r.get("benchmark_universe_version") == BENCHMARK_UNIVERSE_VERSION_LEGACY
            )
        ]
        reasons = [r for r in reasons if r not in legacy_reasons]
        reasons.extend((v2 or {}).get("reasons") or [])
        # An unpriced LEGACY-only symbol (e.g. EUR/USD in the old basket) no
        # longer blocks; an unpriced name in the live book or in a v2 universe
        # still does.
        still_required = sorted(set(pf_symbols) | set(v2_universe))
        blocking_unpriced = [s for s in still_required if not res.prices.get(s)]
        if blocking_unpriced:
            reasons.append(_reason("unpriced_symbols", symbols=blocking_unpriced))
        v2_eligible = sorted(v2_held.get("universe") or [])
        v2_extra = sorted(set(v2_eligible) - set(pf_symbols))
        v2_missing = sorted(set(pf_symbols) - set(v2_eligible))
        if v2_extra or v2_missing:
            universe_mismatch = True
            reasons.append(
                _reason(
                    "universe_mismatch",
                    benchmark_universe_version="%s-%s" % (eqw_v2.VERSION, eqw_v2.HELD_KEY),
                    not_in_portfolio=v2_extra,
                    not_in_benchmark=v2_missing,
                    benchmark_size=len(v2_eligible),
                    portfolio_size=len(pf_symbols),
                )
            )
        v2_unpriced = sorted(
            set(v2_held.get("unpriced") or []) | set(v2_mandate.get("unpriced") or [])
        )
        if v2_unpriced:
            reasons.append(
                _reason("eqw_v2_unpriced_symbols", symbols=v2_unpriced)
            )
    elif v2:
        reasons.extend((v2 or {}).get("reasons") or [])

    # ── declared cash / initial capital ───────────────────────────────────
    pf_capital = pf.get("initial_capital")
    bm_capital = bm.get("initial_capital") if bm else None
    if (
        pf_capital is not None
        and bm_capital is not None
        and float(pf_capital) != float(bm_capital)
    ):
        reasons.append(
            _reason(
                "initial_capital_mismatch",
                portfolio_initial_capital=pf_capital,
                benchmark_initial_capital=bm_capital,
            )
        )

    initial_capital = float(pf_capital or bm_capital or 0.0)
    cash_policy = {
        "portfolio_cash": pf.get("cash"),
        "portfolio_cash_pct": (
            round(float(pf["cash"]) / float(pf_value) * 100.0, 2)
            if pf_value and pf.get("cash") is not None
            else None
        ),
        "benchmark_cash_pct": 0.0,
        "note": "benchmark is a fully-invested buy-and-hold by construction",
    }

    # The gate is measured against the RECORDED benchmark snapshot for the
    # same as-of date — the eqw-v2 row when it covers the date, else the legacy
    # record. A recomputed value is reported for cross-check only: the
    # benchmark history is evidence and is never silently replaced.
    basis: Optional[str] = None
    bm_value: Optional[float] = None
    benchmark_source = "eqw-v1-legacy"
    if use_v2 and as_of_date is not None:
        bm_value = v2_held.get("value")
        basis = "eqw_v2_recorded_snapshot_at_as_of"
        benchmark_source = eqw_v2.VERSION
    elif bm_last is not None and bm_last_date is not None and bm_last_date == as_of_date:
        bm_value = bm_last_value
        basis = "recorded_snapshot_at_as_of"
    elif recomputed is not None:
        bm_value = None
        basis = "no_recorded_snapshot_at_as_of"

    # Both sides are already net of their modelled costs under eqw-v2, so no
    # extra round-trip charge is applied (the caller's policy, when given,
    # still wins).
    if use_v2 and not cost_policy:
        cost = dict(DEFAULT_COST_POLICY)
        cost.update(V2_COST_POLICY)

    comparable = not reasons and pf_value is not None and bm_value is not None

    payload: dict[str, Any] = {
        "comparison_status": "comparable" if comparable else "incomparable",
        "reasons": reasons,
        "reason_codes": [r["code"] for r in reasons],
        "as_of": as_of_date.isoformat() if as_of_date else None,
        "as_of_source": "canonical portfolio status (portfolio_status.current_status)",
        "price_date": res.price_date,
        "eligible_universe": sorted(pf_symbols),
        "eligible_universe_size": len(pf_symbols),
        "benchmark_universe": sorted(bm_universe),
        "benchmark_universe_version": bm_version,
        "benchmark_age_days": benchmark_age_days,
        "universe_mismatch": universe_mismatch,
        "benchmark_basis": basis,
        "benchmark_source": benchmark_source,
        "benchmark_snapshot_date": (
            v2_held.get("snapshot_date") if use_v2
            else (bm_last_date.isoformat() if bm_last_date else None)
        ),
        "cost_policy": cost,
        "cash_policy": cash_policy,
        "resolver": res.provenance(),
        "initial_capital": initial_capital,
        "portfolio": {
            "value": pf_value,
            "cash": pf.get("cash"),
            "return_pct": (
                round((float(pf_value) / initial_capital - 1.0) * 100.0, 4)
                if pf_value is not None and initial_capital > 0
                else None
            ),
            "position_count": pf.get("position_count"),
            "as_of": pf.get("as_of"),
        },
        "benchmark": {
            "value": bm_value,
            "return_pct": (
                round((bm_value / initial_capital - 1.0) * 100.0, 4)
                if bm_value is not None and initial_capital > 0
                else None
            ),
            "recorded_value": bm_last_value,
            "recorded_date": bm_last_date.isoformat() if bm_last_date else None,
            "recomputed_value": recomputed.get("value") if recomputed else None,
            "recomputed_return_pct": recomputed.get("return_pct") if recomputed else None,
            "recomputed_used_for_verdict": False,
            "recomputed_missing_prices": recomputed.get("missing_prices") if recomputed else None,
            "recomputed_missing_init_prices": (
                recomputed.get("missing_init_prices") if recomputed else None
            ),
        },
        "gap_gross_pct": None,
        "gap_net_pct": None,
        "gate_ok": None,
        "gate_deadline": GATE_DEADLINE,
        "verdict": "BENCHMARK COMPARISON INCOMPARABLE",
    }

    if comparable:
        gross = payload["portfolio"]["return_pct"] - payload["benchmark"]["return_pct"]
        net = gross - (
            float(cost["strategy_round_trip_cost_pct"])
            - float(cost["benchmark_round_trip_cost_pct"])
        )
        payload["gap_gross_pct"] = round(gross, 4)
        payload["gap_net_pct"] = round(net, 4)
        payload["gate_ok"] = bool(net >= 0)
        payload["verdict"] = "ON TRACK" if payload["gate_ok"] else "BEHIND GATE"

    # ── continuity + rule inputs ──────────────────────────────────────────
    payload["benchmark"]["source"] = benchmark_source
    payload["benchmark"]["version"] = eqw_v2.VERSION if use_v2 else bm_version
    payload["benchmark"]["init_date"] = (
        (v2_record or {}).get("init_date") if use_v2 else bm.get("init_date") if bm else None
    )
    payload["benchmark_v2"] = v2
    payload["legacy_benchmark"] = {
        "universe_version": bm_version,
        "universe": sorted(bm_universe),
        "recorded_value": bm_last_value,
        "recorded_date": bm_last_date.isoformat() if bm_last_date else None,
        "recomputed_value": recomputed.get("value") if recomputed else None,
        "reported_reasons": legacy_reasons,
        "demoted_from_verdict": bool(use_v2),
        "role": (
            "continuity only — append-only history preserved, not the gate benchmark"
            if use_v2 else "verdict source (no covered eqw-v2 record)"
        ),
    }
    if use_v2 and v2 is not None:
        drawdown = eqw_v2.strategy_max_drawdown(
            dir_path,
            start=(v2_record or {}).get("init_date"),
            end=as_of_date,
            snapshots=snapshots,
        )
        payload["strategy_drawdown"] = drawdown
        payload["stage1_rule"] = stage1_rule_block(
            strategy_return_pct=payload["portfolio"]["return_pct"],
            benchmark_return_pct=payload["benchmark"]["return_pct"],
            gap_net_pct=payload["gap_net_pct"],
            window_sessions=int(v2.get("window_sessions") or 0),
            minimum_window_sessions=int(v2.get("minimum_window_sessions") or min_window_sessions),
            drawdown_pct=drawdown.get("max_drawdown_pct"),
            mandate_return_pct=(v2_benchmarks.get(eqw_v2.MANDATE_KEY) or {}).get("return_pct"),
        )
    else:
        payload["strategy_drawdown"] = None
        payload["stage1_rule"] = None
    return payload


def reason_summary(cmp: dict[str, Any]) -> str:
    """One-line machine-readable summary of why a comparison is blocked."""
    if cmp.get("comparison_status") == "comparable":
        return "comparable"
    codes: list[str] = []
    for r in cmp.get("reasons") or []:
        if r["code"] == "benchmark_record_stale":
            codes.append(f"benchmark_age_days={r.get('benchmark_age_days')}")
        elif r["code"] == "window_below_minimum":
            codes.append(
                "window_below_minimum=%s/%s"
                % (r.get("window_sessions"), r.get("minimum_window_sessions"))
            )
        elif r["code"] == "universe_mismatch":
            codes.append("universe_mismatch")
        else:
            codes.append(str(r["code"]))
    return ", ".join(codes) if codes else "unknown"


def _v2_lines(cmp: dict[str, Any]) -> list[str]:
    """Benchmark-v2 evidence lines (versioned record), when present."""
    v2 = cmp.get("benchmark_v2") or {}
    if not v2.get("available"):
        return []
    lines = [
        "  Benchmark:    eqw-v2 (record %s, init %s, window %s/%s sessions)"
        % (v2.get("version"), v2.get("init_date"), v2.get("window_sessions"),
           v2.get("minimum_window_sessions"))
    ]
    for key in (eqw_v2.HELD_KEY, eqw_v2.MANDATE_KEY):
        b = (v2.get("benchmarks") or {}).get(key) or {}
        lines.append(
            "    eqw-v2-%s: unmoved_value=%s net_return=%s%% (last %s)"
            % (key, b.get("value"), b.get("return_pct"), b.get("snapshot_date"))
        )
        for e in b.get("excluded") or []:
            lines.append(
                "      excluded: %s (%s)" % (e.get("symbol"), e.get("reason"))
            )
    rule = cmp.get("stage1_rule") or {}
    if rule:
        clauses = rule.get("clauses") or {}
        lines.append(
            "  Rule inputs:  strategy_net=%s%%  eqw-v2-held_net=%s%%  "
            "mandate_net=%s%%  max_dd=%s%%"
            % (rule.get("strategy_net_return_pct"), rule.get("benchmark_net_return_pct"),
               rule.get("mandate_net_return_pct"), rule.get("max_drawdown_pct"))
        )
        for name, c in clauses.items():
            mark = {True: "PASS", False: "FAIL", None: "UNKNOWN"}[c.get("pass")]
            lines.append("    %-28s %s" % (name, mark))
        lines.append(
            "  Rule outcome (if evaluated now): %s" % rule.get("outcome_if_evaluated_now")
        )
    return lines


def format_comparison(cmp: dict[str, Any]) -> list[str]:
    """Plain-text lines for the comparison (no Rich/Telegram markup)."""
    lines: list[str] = []
    if cmp.get("comparison_status") != "comparable":
        lines.append(
            "BENCHMARK COMPARISON INCOMPARABLE — " + reason_summary(cmp)
        )
        for r in cmp.get("reasons") or []:
            lines.append("  reason: " + str(r.get("detail")))
        bm = cmp.get("benchmark") or {}
        if bm.get("recorded_date"):
            lines.append(
                "  benchmark record: KES %s @ %s (%s)"
                % (bm.get("recorded_value"), bm.get("recorded_date"),
                   cmp.get("benchmark_universe_version"))
            )
        lines.extend(_v2_lines(cmp))
        lines.append(
            "  no gap is published from a mismatched date/universe"
        )
        return lines
    lines.append("BENCHMARK COMPARISON (same-date, same-universe, same resolver)")
    lines.append(
        "  As of:        %s (prices %s)" % (cmp.get("as_of"), cmp.get("price_date"))
    )
    lines.append(
        "  Universe:     %d assets, %s"
        % (cmp.get("eligible_universe_size"), cmp.get("benchmark_universe_version"))
    )
    p = cmp["portfolio"]
    b = cmp["benchmark"]
    lines.append(
        "  Portfolio:    %+.2f%%  (KES %s)"
        % (p["return_pct"], f"{p['value']:,.2f}")
    )
    lines.append(
        "  Benchmark:    %+.2f%%  (KES %s @ %s) [%s]"
        % (b["return_pct"], f"{b['value']:,.2f}", b.get("recorded_date"),
           cmp.get("benchmark_source"))
    )
    lines.append(
        "  Gap (gross):  %+.2f%%   net of declared costs: %+.2f%%"
        % (cmp["gap_gross_pct"], cmp["gap_net_pct"])
    )
    lines.extend(_v2_lines(cmp))
    lines.append(
        "  Verdict:      %s   deadline %s"
        % ("ON TRACK" if cmp.get("gate_ok") else "BEHIND GATE", cmp.get("gate_deadline"))
    )
    return lines
