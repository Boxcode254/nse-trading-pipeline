"""TP-005 regression coverage: canonical valuation + same-date benchmark compare.

Deterministic and production-state free: every fixture (mtm stamp, benchmark
history, official AXYS close) is written into ``tmp_path``, so nothing here
reads or writes live ``portfolio/`` state.

Defects this pins down (found 2026-09-15):
  * two read models for one book (``trading portfolio show`` 105,179.75 vs
    ``portfolio/mtm_state.json`` 105,275.28);
  * a precise Stage-1 gate verdict published from a portfolio stamped
    2026-09-14 against a benchmark last snapped 2026-09-09, over a different
    universe (benchmark basket contains EUR/USD, USD/KES, BAMB).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading.price_source import resolve_prices  # noqa: E402
from trading.services import benchmark_compare as bc  # noqa: E402
from trading.services import portfolio_status as ps  # noqa: E402


INITIAL = 100_000.0


def _iso_day(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).date().isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _mtm_stamp(symbols, prices, *, total_value, cash, generated_at=None) -> dict:
    positions = []
    market = 0.0
    for sym in symbols:
        shares = 100
        px = prices[sym]
        value = round(shares * px, 2)
        market += value
        positions.append(
            {
                "symbol": sym,
                "shares": shares,
                "avg_cost": round(px * 0.9, 4),
                "total_cost": round(shares * px * 0.9, 2),
                "live_price": px,
                "change_pct": 0.0,
                "current_value": value,
                "pnl": 0.0,
                "pnl_pct": 0.0,
            }
        )
    return {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "cash": cash,
        "initial_capital": INITIAL,
        "positions": positions,
        "summary": {
            "total_cost_basis": round(market * 0.9, 2),
            "total_market_value": round(market, 2),
            "total_portfolio_value": round(total_value, 2),
            "total_pnl": round(total_value - market * 0.9, 2),
            "total_pnl_pct": 0.0,
            "num_positions": len(positions),
        },
    }


def _build(
    tmp_path: Path,
    *,
    symbols=("AAA", "BBB"),
    prices=None,
    benchmark_assets=None,
    benchmark_date=None,
    benchmark_value=110_000.0,
    benchmark_init=None,
    portfolio_value=105_000.0,
    cash=20_000.0,
    mtm_generated_at=None,
    write_axys_for=None,
):
    """Build a self-consistent fixture portfolio dir. Returns its path."""
    pf_dir = tmp_path / "portfolio"
    prices = prices or {"AAA": 110.0, "BBB": 55.0}
    axys_date = write_axys_for or (mtm_generated_at or datetime.now(timezone.utc).isoformat())[:10]
    _write_json(pf_dir / f"axys_closes_{axys_date}.json", {"axys": dict(prices)})
    _write_json(
        pf_dir / "mtm_state.json",
        _mtm_stamp(
            symbols,
            {s: prices[s] for s in symbols},
            total_value=portfolio_value,
            cash=cash,
            generated_at=mtm_generated_at,
        ),
    )
    assets = list(benchmark_assets or symbols)
    init = benchmark_init or {s: 100.0 for s in assets}
    _write_json(
        pf_dir / "benchmark.json",
        {
            "assets": assets,
            "init_prices": init,
            "initial_capital": INITIAL,
            "snapshots": [
                {
                    "timestamp": f"{benchmark_date or _iso_day()}T19:35:00+03:00",
                    "value": benchmark_value,
                }
            ],
        },
    )
    return pf_dir


# ── 1. canonical read model ───────────────────────────────────────────────
def test_canonical_status_is_single_model_with_price_provenance(tmp_path):
    pf_dir = _build(tmp_path)
    status = ps.current_status(str(pf_dir))
    mtm = json.loads((pf_dir / "mtm_state.json").read_text())

    assert status["portfolio_value"] == mtm["summary"]["total_portfolio_value"]
    assert status["cash"] == mtm["cash"]
    assert status["source"] == ps.SOURCE_MTM
    assert status["eligible_universe"] == ["AAA", "BBB"]
    assert status["as_of_date"] == _iso_day()
    assert status["price_date"] == _iso_day()
    # provenance, per position, from the shared authority chain
    assert [p["price_source"] for p in status["positions"]] == ["axys", "axys"]
    assert status["resolver"]["official_close_file"] == f"axys_closes_{_iso_day()}.json"
    assert status["resolver"]["stale"] is False


def test_canonical_status_missing_state_is_explicit(tmp_path):
    status = ps.current_status(str(tmp_path / "nothing-here"))
    assert status["portfolio_value"] is None
    assert status["current"] is False
    assert status["resolver"] is None
    assert status["eligible_universe"] == []


# ── 2. same-date / same-universe => comparable ────────────────────────────
def test_same_date_same_universe_is_comparable(tmp_path):
    pf_dir = _build(tmp_path)
    cmp = bc.compare(str(pf_dir))

    assert cmp["comparison_status"] == "comparable"
    assert cmp["reason_codes"] == []
    assert cmp["as_of"] == _iso_day()
    assert cmp["price_date"] == _iso_day()
    assert cmp["benchmark_basis"] == "recorded_snapshot_at_as_of"
    assert cmp["eligible_universe"] == ["AAA", "BBB"]
    assert cmp["benchmark_universe"] == ["AAA", "BBB"]
    assert cmp["benchmark_age_days"] == 0
    assert cmp["universe_mismatch"] is False
    # 105,000 / 100,000 = +5% vs 110,000 / 100,000 = +10%
    assert cmp["portfolio"]["return_pct"] == 5.0
    assert cmp["benchmark"]["return_pct"] == 10.0
    assert cmp["gap_gross_pct"] == -5.0
    # declared cost policy: strategy pays one 3% round trip, hold pays none
    assert cmp["cost_policy"]["strategy_round_trip_cost_pct"] == 3.0
    assert cmp["cost_policy"]["benchmark_round_trip_cost_pct"] == 0.0
    assert cmp["gap_net_pct"] == -8.0
    assert cmp["gate_ok"] is False
    assert cmp["verdict"] == "BEHIND GATE"
    # the recomputation is a cross-check, never the verdict input
    assert cmp["benchmark"]["recomputed_used_for_verdict"] is False


def test_comparable_rendering_has_no_incomparable_language(tmp_path):
    pf_dir = _build(tmp_path, benchmark_value=140_000.0)
    cmp = bc.compare(str(pf_dir))
    text = "\n".join(bc.format_comparison(cmp))
    assert cmp["comparison_status"] == "comparable"
    assert "INCOMPARABLE" not in text
    assert cmp["portfolio"]["return_pct"] == 5.0
    assert cmp["benchmark"]["return_pct"] == 40.0
    assert cmp["gap_gross_pct"] == -35.0
    assert cmp["gap_net_pct"] == -38.0


# ── 3. mismatched dates => incomparable, no precise gap ───────────────────
def test_stale_benchmark_record_is_incomparable_with_age_reason(tmp_path):
    stale_date = _iso_day(-5)
    pf_dir = _build(tmp_path, benchmark_date=stale_date, write_axys_for=_iso_day())
    cmp = bc.compare(str(pf_dir))

    assert cmp["comparison_status"] == "incomparable"
    assert "benchmark_record_stale" in cmp["reason_codes"]
    assert cmp["benchmark_age_days"] == 5
    assert cmp["benchmark_snapshot_date"] == stale_date
    assert cmp["gap_gross_pct"] is None
    assert cmp["gap_net_pct"] is None
    assert cmp["gate_ok"] is None
    assert cmp["verdict"] == "BENCHMARK COMPARISON INCOMPARABLE"
    assert cmp["benchmark"]["value"] is None
    assert cmp["benchmark"]["recorded_value"] == 110_000.0
    # diagnostic recompute is still reported, and flagged as not authoritative
    assert cmp["benchmark"]["recomputed_used_for_verdict"] is False

    first = bc.format_comparison(cmp)[0]
    assert first == "BENCHMARK COMPARISON INCOMPARABLE — benchmark_age_days=5"


def test_historical_as_of_does_not_borrow_todays_feed_price(tmp_path):
    """The resolver must not price a past as-of date off a live feed stamp."""
    pf_dir = _build(tmp_path, symbols=("AAA",))
    past = _iso_day(-30)
    res = resolve_prices(["AAA"], str(pf_dir), as_of=past)
    assert res.prices == {}
    assert res.sources == {}
    # as of today the same symbol resolves off the AXYS close
    today_res = resolve_prices(["AAA"], str(pf_dir), as_of=_iso_day())
    assert today_res.prices == {"AAA": 110.0}
    assert today_res.sources == {"AAA": "axys"}


# ── 4. universe mismatch => incomparable ─────────────────────────────────
def test_universe_mismatch_is_incomparable(tmp_path):
    pf_dir = _build(
        tmp_path,
        symbols=("AAA", "BBB"),
        benchmark_assets=["AAA", "BBB", "EUR/USD"],
        benchmark_init={"AAA": 100.0, "BBB": 100.0, "EUR/USD": 1.1},
    )
    cmp = bc.compare(str(pf_dir))

    assert cmp["comparison_status"] == "incomparable"
    assert cmp["universe_mismatch"] is True
    assert "universe_mismatch" in cmp["reason_codes"]
    assert cmp["benchmark_universe"] == ["AAA", "BBB", "EUR/USD"]
    assert cmp["eligible_universe"] == ["AAA", "BBB"]
    assert cmp["gap_net_pct"] is None
    detail = next(r for r in cmp["reasons"] if r["code"] == "universe_mismatch")
    assert detail["not_in_portfolio"] == ["EUR/USD"]
    assert detail["not_in_benchmark"] == []
    assert "universe_mismatch" in bc.format_comparison(cmp)[0]


# ── 5. stale portfolio => incomparable (symmetric honesty) ───────────────
def test_stale_portfolio_is_incomparable(tmp_path):
    stamp = (datetime.now(timezone.utc) - timedelta(days=6))
    pf_dir = _build(
        tmp_path,
        mtm_generated_at=stamp.isoformat(),
        benchmark_date=stamp.date().isoformat(),
        write_axys_for=stamp.date().isoformat(),
    )
    cmp = bc.compare(str(pf_dir))

    assert cmp["comparison_status"] == "incomparable"
    assert "portfolio_stale" in cmp["reason_codes"]
    # dates themselves match, so this is not a date-mismatch block
    assert cmp["benchmark_age_days"] == 0
    assert cmp["gap_gross_pct"] is None


# ── 6. explicit universe injection keeps the contract testable ───────────
def test_explicit_eligible_universe_is_honoured(tmp_path):
    pf_dir = _build(
        tmp_path,
        symbols=("AAA", "BBB"),
        benchmark_assets=["AAA"],
        benchmark_init={"AAA": 100.0},
    )
    cmp = bc.compare(str(pf_dir), eligible_universe=["AAA"])
    assert cmp["eligible_universe"] == ["AAA"]
    assert cmp["universe_mismatch"] is False
    assert cmp["comparison_status"] == "comparable"


# ── 7. read-only guarantee ───────────────────────────────────────────────
def test_compare_does_not_mutate_runtime_state(tmp_path):
    pf_dir = _build(tmp_path)
    before = {
        p.name: p.read_text()
        for p in sorted(pf_dir.glob("*.json"))
    }
    bc.compare(str(pf_dir))
    ps.current_status(str(pf_dir))
    after = {p.name: p.read_text() for p in sorted(pf_dir.glob("*.json"))}
    assert before == after
