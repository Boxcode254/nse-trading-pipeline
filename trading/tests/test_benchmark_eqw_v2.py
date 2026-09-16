"""Task 6 regression coverage: the versioned eqw-v2 benchmark tracker.

Deterministic and production-state free: every fixture (AXYS close, mtm stamp,
legacy benchmark record, eqw-v2 record) is written into ``tmp_path``, so nothing
here reads or writes live ``portfolio/`` state.

What this pins down:
  * init captures init prices ONCE from the authority chain and RECORDS an
    unobtainable member (BRIT has no CSV) as an exclusion instead of failing or
    silently shrinking the basket;
  * tracking is idempotent per date (upsert, never a second row for one session);
  * the cost-adjusted value formula is the hand-computable one
    ``capital × mean(price_t / price_init) − entry_cost``;
  * ``compare()`` sources the verdict from the eqw-v2 record when it covers the
    valuation date, demotes the legacy record's own defects to
    ``legacy_benchmark``, and surfaces ``window_below_minimum`` when the window
    is shorter than the pre-registered 20 sessions.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading import config as trading_config  # noqa: E402
from trading.services import benchmark_compare as bc  # noqa: E402
from trading.services import benchmark_eqw_v2 as eqw2  # noqa: E402

INITIAL = 100_000.0


# ── fixture helpers ───────────────────────────────────────────────────────────
def _day(offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset)).date().isoformat()


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _axys(pf_dir: Path, day: str, prices: dict) -> None:
    _write(pf_dir / f"axys_closes_{day}.json", {"axys": dict(prices)})


def _mtm(pf_dir: Path, symbols, prices, total_value, cash=0.0, generated_at=None) -> None:
    positions = [
        {
            "symbol": s,
            "shares": 100,
            "avg_cost": prices[s],
            "total_cost": round(100 * prices[s], 2),
            "live_price": prices[s],
            "change_pct": 0.0,
            "current_value": round(100 * prices[s], 2),
        }
        for s in symbols
    ]
    _write(
        pf_dir / "mtm_state.json",
        {
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            "cash": cash,
            "initial_capital": INITIAL,
            "positions": positions,
            "summary": {
                "total_portfolio_value": round(total_value, 2),
                "num_positions": len(positions),
            },
        },
    )


def _legacy(pf_dir: Path, assets, init_prices, snapshot_day: str, value: float) -> None:
    _write(
        pf_dir / "benchmark.json",
        {
            "assets": list(assets),
            "init_prices": dict(init_prices),
            "initial_capital": INITIAL,
            "snapshots": [{"timestamp": f"{snapshot_day}T19:35:00+03:00", "value": value}],
        },
    )


def _pf_dir(tmp_path: Path) -> Path:
    pf = tmp_path / "portfolio"
    pf.mkdir(parents=True, exist_ok=True)
    return pf


# ── 1. init: capture once + never-silent exclusion ───────────────────────────
def test_init_captures_prices_and_records_exclusions(tmp_path):
    pf = _pf_dir(tmp_path)
    day = _day()
    _axys(pf, day, {"AAA": 100.0, "BBB": 200.0})
    _mtm(pf, ["AAA", "BBB"], {"AAA": 100.0, "BBB": 200.0}, 30_000.0)

    out = eqw2.init(
        str(pf), as_of=day, held=["AAA", "BBB"], mandate=["AAA", "BBB", "BRIT"]
    )
    rec = out["record"]

    assert out["initialized"] is True and out["written"] is True
    assert rec["version"] == "eqw-v2"
    assert rec["init_date"] == day
    assert rec["initial_capital"] == INITIAL
    assert rec["benchmarks"]["held"]["universe"] == ["AAA", "BBB"]
    assert rec["benchmarks"]["held"]["init_prices"] == {"AAA": 100.0, "BBB": 200.0}
    assert rec["benchmarks"]["held"]["excluded"] == []
    assert rec["benchmarks"]["held"]["snapshots"] == []
    # BRIT has no price anywhere -> excluded with a reason, never a failure
    mandate = rec["benchmarks"]["mandate"]
    assert mandate["universe"] == ["AAA", "BBB"]
    assert [e["symbol"] for e in mandate["excluded"]] == ["BRIT"]
    assert mandate["excluded"][0]["reason"] == eqw2.EXCLUDED_NO_PRICE
    assert "BRIT" in mandate["excluded"][0]["detail"]
    # persisted to its own record file, and only there
    on_disk = eqw2.load_record(str(pf))
    assert on_disk["benchmarks"]["mandate"]["excluded"][0]["symbol"] == "BRIT"
    assert sorted(p.name for p in pf.glob("*.json")) == [
        "axys_closes_%s.json" % day,
        "benchmark_eqw_v2.json",
        "mtm_state.json",
    ]
    # entry cost uses the engine's single cost model (fee 1500 + slip 150)
    assert rec["benchmarks"]["held"]["entry_cost_kes"] == 1650.0


def test_init_is_write_once_unless_forced(tmp_path):
    pf = _pf_dir(tmp_path)
    day = _day()
    _axys(pf, day, {"AAA": 100.0})
    first = eqw2.init(str(pf), as_of=day, held=["AAA"], mandate=["AAA"])
    assert first["initialized"] is True

    # a later run must NOT re-capture (that is a window reset, needs approval)
    _axys(pf, day, {"AAA": 999.0})
    second = eqw2.init(str(pf), as_of=day, held=["AAA"], mandate=["AAA"])
    assert second["initialized"] is False and second["written"] is False
    assert second["record"]["benchmarks"]["held"]["init_prices"] == {"AAA": 100.0}

    forced = eqw2.init(str(pf), as_of=day, held=["AAA"], mandate=["AAA"], force=True)
    assert forced["initialized"] is True
    assert forced["record"]["benchmarks"]["held"]["init_prices"] == {"AAA": 999.0}


def test_init_refuses_an_empty_universe(tmp_path):
    pf = _pf_dir(tmp_path)
    _mtm(pf, [], {}, 100_000.0)
    try:
        eqw2.init(str(pf), as_of=_day(), held=[], mandate=["AAA"])
    except ValueError as exc:
        assert "empty benchmark universe" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("init() must refuse to benchmark nothing")


def test_mandate_universe_is_tradable_strategy_members():
    mandate = eqw2.mandate_universe()
    assert mandate == sorted(mandate)
    assert "BRIT" in mandate          # tradable, just unpriced by the pipeline
    assert "BAMB" not in mandate      # suspended -> never in a benchmark universe


# ── 2. track: idempotent upsert ───────────────────────────────────────────────
def test_track_upserts_same_date_to_a_single_row(tmp_path):
    pf = _pf_dir(tmp_path)
    day = _day()
    _axys(pf, day, {"AAA": 100.0, "BBB": 200.0})
    eqw2.init(str(pf), as_of=day, held=["AAA", "BBB"], mandate=["AAA", "BBB"])

    first = eqw2.track(day, {"AAA": 110.0, "BBB": 180.0}, str(pf))
    assert first["written"] is True
    second = eqw2.track(day, {"AAA": 120.0, "BBB": 180.0}, str(pf))

    rec = eqw2.load_record(str(pf))
    snaps = rec["benchmarks"]["held"]["snapshots"]
    assert len(snaps) == 1, "one session must never produce two rows"
    assert snaps[0]["date"] == day
    assert snaps[0]["value"] == second["results"]["held"]["value"]
    assert snaps[0]["value"] != first["results"]["held"]["value"]
    assert snaps[0]["value"] == round(INITIAL * ((1.2 + 0.9) / 2) - 1650.0, 2)


def test_track_never_silently_shrinks_the_basket(tmp_path):
    pf = _pf_dir(tmp_path)
    day = _day()
    _axys(pf, day, {"AAA": 100.0, "BBB": 200.0})
    eqw2.init(str(pf), as_of=day, held=["AAA", "BBB"], mandate=["AAA", "BBB"])

    out = eqw2.track(day, {"AAA": 110.0}, str(pf))
    assert out["results"]["held"]["value"] is None
    assert out["results"]["held"]["unpriced"] == ["BBB"]
    assert out["results"]["held"]["recorded"] is False
    assert out["written"] is False
    assert eqw2.load_record(str(pf))["benchmarks"]["held"]["snapshots"] == []


def test_track_requires_an_initialised_record(tmp_path):
    pf = _pf_dir(tmp_path)
    try:
        eqw2.track(_day(), {"AAA": 1.0}, str(pf))
    except eqw2.BenchmarkNotInitialized as exc:
        assert "run init() first" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("track() before init() must be an explicit error")


# ── 3. cost-adjusted value math (hand-computed) ──────────────────────────────
def test_cost_adjusted_value_math_matches_hand_computation(tmp_path):
    pf = _pf_dir(tmp_path)
    day = _day()
    # init: 100 / 200 / 50 (mean 116.667) ; at t: 110 / 180 / 60
    _axys(pf, day, {"AAA": 100.0, "BBB": 200.0, "CCC": 50.0})
    eqw2.init(
        str(pf), as_of=day, held=["AAA", "BBB", "CCC"], mandate=["AAA", "BBB", "CCC"]
    )
    out = eqw2.track(day, {"AAA": 110.0, "BBB": 180.0, "CCC": 60.0}, str(pf))

    # mean ratios = (1.1 + 0.9 + 1.2) / 3 = 1.06666... -> 106,666.67 gross
    cost = trading_config.trade_cost(INITIAL, (100.0 + 200.0 + 50.0) / 3)["total"]
    assert cost == 1650.0
    expected = round(INITIAL * (1.1 + 0.9 + 1.2) / 3 - cost, 2)
    assert expected == 105_016.67

    held = out["results"]["held"]
    assert held["value"] == expected
    assert held["return_pct"] == 5.0167
    # identical construction for both benchmarks
    assert out["results"]["mandate"]["value"] == expected
    rec = eqw2.load_record(str(pf))
    assert rec["benchmarks"]["held"]["snapshots"][0]["value"] == expected
    assert rec["benchmarks"]["mandate"]["snapshots"][0]["value"] == expected


def test_daily_track_initialises_then_tracks(tmp_path):
    pf = _pf_dir(tmp_path)
    day = _day()
    _axys(pf, day, {"AAA": 100.0})
    _mtm(pf, ["AAA"], {"AAA": 100.0}, 10_000.0)

    payload = eqw2.daily_track(str(pf), as_of=day)
    assert "error" not in payload
    assert payload["init"]["initialized"] is True
    assert payload["track"]["results"]["held"]["recorded"] is True
    # second run of the same session: no re-init, still one row
    payload2 = eqw2.daily_track(str(pf), as_of=day)
    assert payload2["init"] is None
    assert len(eqw2.load_record(str(pf))["benchmarks"]["held"]["snapshots"]) == 1


# ── 4. window / drawdown rule inputs ─────────────────────────────────────────
def test_window_sessions_counts_rows_between_init_and_valuation():
    rec = {
        "version": "eqw-v2",
        "init_date": "2026-09-01",
        "initial_capital": INITIAL,
        "benchmarks": {
            "held": {
                "universe": ["AAA"],
                "init_prices": {"AAA": 1.0},
                "excluded": [],
                "snapshots": [
                    {"date": "2026-08-31", "value": 1.0},   # before init: ignored
                    {"date": "2026-09-01", "value": 1.0},
                    {"date": "2026-09-02", "value": 1.0},
                    {"date": "2026-09-10", "value": 1.0},   # after valuation: ignored
                ],
            },
            "mandate": {"universe": ["AAA"], "init_prices": {"AAA": 1.0},
                        "excluded": [], "snapshots": []},
        },
    }
    assert eqw2.window_sessions(rec, "held", through="2026-09-02") == 2
    a = eqw2.assess(rec, "2026-09-02")
    assert a["covers_valuation_date"] is False  # mandate has no row that day
    assert a["window_sessions"] == 2
    assert a["below_minimum_window"] is True
    assert [r["code"] for r in a["reasons"]] == [
        "eqw_v2_no_snapshot_at_valuation_date",
        "window_below_minimum",
    ]


def test_strategy_max_drawdown_from_snapshot_series(tmp_path):
    pf = _pf_dir(tmp_path)
    series = [
        {"timestamp": "2026-09-01T15:30:00+03:00", "total_value": 100_000.0},
        {"timestamp": "2026-09-02T15:30:00+03:00", "total_value": 110_000.0},
        {"timestamp": "2026-09-03T15:30:00+03:00", "total_value": 93_500.0},
        {"timestamp": "2026-09-04T15:30:00+03:00", "total_value": 105_000.0},
    ]
    out = eqw2.strategy_max_drawdown(str(pf), "2026-09-01", "2026-09-04", snapshots=series)
    assert out["max_drawdown_pct"] == 15.0   # peak 110k -> trough 93.5k
    assert out["points"] == 4
    # missing series is an explicit reason, never a fabricated 0.0
    none_out = eqw2.strategy_max_drawdown(str(pf), snapshots=None)
    assert none_out["max_drawdown_pct"] is None
    assert none_out["reason"] == "snapshots_unavailable"


# ── 5. compare() integration ─────────────────────────────────────────────────
def _v2_record(pf: Path, init_day: str, days: list, init_prices: dict,
               end_prices: dict) -> dict:
    _axys(pf, init_day, init_prices)
    eqw2.init(str(pf), as_of=init_day,
              held=sorted(init_prices), mandate=sorted(init_prices))
    for d in days:
        eqw2.track(d, end_prices, str(pf))
    return eqw2.load_record(str(pf))


def test_compare_uses_v2_record_and_demotes_legacy(tmp_path):
    pf = _pf_dir(tmp_path)
    today = _day()
    init_day = _day(-24)
    init_prices = {"AAA": 100.0, "BBB": 200.0}
    end_prices = {"AAA": 110.0, "BBB": 180.0}
    sessions = [_day(-24 + i) for i in range(25)]  # 25 sessions, >= 20
    _axys(pf, today, end_prices)
    _mtm(pf, ["AAA", "BBB"], end_prices, 105_000.0, cash=20_000.0)
    # legacy record deliberately stale (5 sessions old) and mismatched
    _legacy(pf, ["AAA", "BBB", "EUR/USD"],
            {"AAA": 100.0, "BBB": 100.0, "EUR/USD": 1.1}, _day(-5), 110_000.0)
    rec = _v2_record(pf, init_day, sessions, init_prices, end_prices)
    assert rec["init_date"] == init_day

    cmp = bc.compare(
        str(pf),
        snapshots=[
            {"timestamp": f"{_day(-24)}T15:30:00+03:00", "total_value": 100_000.0},
            {"timestamp": f"{today}T15:30:00+03:00", "total_value": 105_000.0},
        ],
    )

    assert cmp["benchmark_source"] == "eqw-v2"
    assert cmp["benchmark_basis"] == "eqw_v2_recorded_snapshot_at_as_of"
    assert cmp["benchmark_snapshot_date"] == today
    # legacy staleness + legacy-basket mismatch no longer block: they describe
    # the OLD basket. They are reported, not dropped.
    assert cmp["reason_codes"] == []
    assert cmp["comparison_status"] == "comparable"
    legacy = cmp["legacy_benchmark"]
    assert legacy["demoted_from_verdict"] is True
    assert "benchmark_record_stale" in [r["code"] for r in legacy["reported_reasons"]]
    # the legacy basket's unpriced EUR/USD is demoted, not dropped
    assert any(
        r["code"] == "unpriced_symbols" and "EUR/USD" in r.get("symbols", [])
        for r in legacy["reported_reasons"]
    )
    assert legacy["recorded_value"] == 110_000.0

    # both sides net: strategy 105k/100k = +5.0%, eqw-v2-held 100k-1650 = -1.65%
    assert cmp["portfolio"]["return_pct"] == 5.0
    assert cmp["benchmark"]["return_pct"] == -1.65
    assert cmp["cost_policy"]["strategy_round_trip_cost_pct"] == 0.0
    assert cmp["gap_net_pct"] == 6.65
    assert cmp["benchmark_v2"]["window_sessions"] == 25
    assert cmp["benchmark_v2"]["minimum_window_sessions"] == 20
    assert cmp["benchmark_v2"]["below_minimum_window"] is False
    # mandate universe = same two names here, so context return matches
    assert cmp["benchmark_v2"]["benchmarks"]["mandate"]["return_pct"] == -1.65
    # every rule input the pre-registered rule needs is exposed
    rule = cmp["stage1_rule"]
    assert rule["window_sessions"] == 25
    assert rule["strategy_net_return_pct"] == 5.0
    assert rule["benchmark_net_return_pct"] == -1.65
    assert rule["mandate_net_return_pct"] == -1.65
    assert rule["max_drawdown_pct"] == 0.0
    assert rule["clauses"]["window_minimum_met"]["pass"] is True
    assert rule["clauses"]["strategy_net_return_ge_zero"]["pass"] is True
    assert rule["clauses"]["strategy_beats_eqw_v2_held"]["pass"] is True
    assert rule["clauses"]["max_drawdown_below_limit"]["pass"] is True
    assert rule["outcome_if_evaluated_now"] == "GO"
    text = "\n".join(bc.format_comparison(cmp))
    assert "eqw-v2" in text and "window_minimum_met" in text


def test_compare_flags_window_below_minimum(tmp_path):
    pf = _pf_dir(tmp_path)
    today = _day()
    init_day = _day(-2)
    init_prices = {"AAA": 100.0, "BBB": 200.0}
    end_prices = {"AAA": 110.0, "BBB": 180.0}
    _axys(pf, today, end_prices)
    _mtm(pf, ["AAA", "BBB"], end_prices, 105_000.0)
    _legacy(pf, ["AAA", "BBB"], init_prices, today, 100_000.0)
    _v2_record(pf, init_day, [_day(-2), _day(-1), today], init_prices, end_prices)

    cmp = bc.compare(str(pf))

    assert cmp["comparison_status"] == "incomparable"
    assert "window_below_minimum" in cmp["reason_codes"]
    detail = next(r for r in cmp["reasons"] if r["code"] == "window_below_minimum")
    assert detail["window_sessions"] == 3
    assert detail["minimum_window_sessions"] == 20
    assert cmp["gap_net_pct"] is None and cmp["gate_ok"] is None
    assert cmp["benchmark_v2"]["below_minimum_window"] is True
    assert cmp["stage1_rule"]["outcome_if_evaluated_now"] == "INSUFFICIENT_WINDOW"
    assert cmp["stage1_rule"]["clauses"]["window_minimum_met"]["pass"] is False
    assert "window_below_minimum=3/20" in bc.format_comparison(cmp)[0]


def test_compare_without_v2_record_is_unchanged_legacy(tmp_path):
    pf = _pf_dir(tmp_path)
    today = _day()
    prices = {"AAA": 100.0, "BBB": 200.0}
    _axys(pf, today, prices)
    _mtm(pf, ["AAA", "BBB"], prices, 105_000.0)
    _legacy(pf, ["AAA", "BBB"], prices, today, 110_000.0)

    cmp = bc.compare(str(pf))

    assert cmp["benchmark_source"] == "eqw-v1-legacy"
    assert cmp["benchmark_v2"] is None
    assert cmp["stage1_rule"] is None and cmp["strategy_drawdown"] is None
    assert cmp["legacy_benchmark"]["demoted_from_verdict"] is False
    assert cmp["legacy_benchmark"]["role"].startswith("verdict source")
    # legacy cost policy is untouched (declared 3% strategy round trip)
    assert cmp["cost_policy"]["strategy_round_trip_cost_pct"] == 3.0
    assert cmp["gap_net_pct"] == -8.0


def test_compare_and_track_do_not_mutate_the_v2_record(tmp_path):
    pf = _pf_dir(tmp_path)
    today = _day()
    prices = {"AAA": 100.0, "BBB": 200.0}
    _axys(pf, today, prices)
    _mtm(pf, ["AAA", "BBB"], prices, 105_000.0)
    _legacy(pf, ["AAA", "BBB"], prices, today, 110_000.0)
    _v2_record(pf, today, [today], prices, prices)

    path = eqw2.record_path(str(pf))
    before = path.read_text()
    bc.compare(str(pf))
    eqw2.load_record(str(pf))
    eqw2.assess(eqw2.load_record(str(pf)), today)
    assert path.read_text() == before
