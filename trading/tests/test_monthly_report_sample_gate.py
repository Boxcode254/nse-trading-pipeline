"""TP-006 — the monthly report must not claim performance on a tiny sample.

Fixture-only: a tmp learning.db is populated through the real LearningDB class
and the report is generated from it; no production database or log is touched.
"""
from __future__ import annotations

import json

import pytest

from learning import monthly_report
from learning.db import LearningDB, Outcome, Recommendation
from learning.monthly_report import generate_monthly_report


def _seed(path, n_recs: int, n_eval: int, n_win: int) -> LearningDB:
    """Populate a tmp learning.db; recommendations span two calendar months."""
    db = LearningDB(db_path=path)
    for i in range(n_recs):
        db.add_recommendation(Recommendation(
            symbol="SYM%02d" % i,
            date=_date(i, n_recs),
            confidence=0.6,
            recommendation="BUY" if i % 3 else "SELL",
            score=50.0,
        ))
    for i in range(n_eval):
        db.add_outcome(Outcome(
            symbol="SYM%02d" % i,
            date=_date(i, n_recs),
            market_outcome="UP",
            expected_return=5.0,
            actual_return=4.0,
            time_to_target=10,
            success=i < n_win,
        ))
    return db


def _date(i: int, n_recs: int) -> str:
    month = "07" if i < n_recs // 2 else "08"
    return "2026-%s-%02d" % (month, (i % 28) + 1)


def test_insufficient_sample_blocks_the_success_claim(tmp_path):
    db = _seed(tmp_path / "learning.db", n_recs=20, n_eval=4, n_win=4)
    report = generate_monthly_report(db, months_back=3)

    assert "insufficient_sample (evaluated=4 of 20 recommendations, 20.00% coverage)" in report
    assert "**Success Rate:**" not in report
    assert "producing profitable signals more often than not" not in report
    assert "Winning trades are outweighing losses" not in report
    assert "Targets reached quickly" not in report
    assert "Month-over-month performance is not assessed" in report
    assert "per-symbol performance ranking is suppressed" in report
    assert "retirement candidates are not named" in report
    # raw counts stay visible
    assert "**Total Recommendations:** 20" in report
    assert "**Evaluated Outcomes:** 4" in report
    assert "**Evaluated Coverage:** 20.00% (4 of 20 recommendations)" in report
    assert "Raw counts only:" in report


def test_sufficient_sample_publishes_the_ratio(tmp_path):
    db = _seed(tmp_path / "learning.db", n_recs=60, n_eval=30, n_win=18)
    report = generate_monthly_report(db, months_back=3)

    assert "**Success Rate:** 60.0% (18/30 evaluated)" in report
    assert "insufficient_sample" not in report


def test_report_at_the_threshold_boundary(tmp_path):
    below = _seed(tmp_path / "below.db", n_recs=40, n_eval=29, n_win=29)
    assert "insufficient_sample" in generate_monthly_report(below, months_back=3)

    at = _seed(tmp_path / "at.db", n_recs=40, n_eval=30, n_win=30)
    at_report = generate_monthly_report(at, months_back=3)
    assert "insufficient_sample" not in at_report
    assert "**Success Rate:** 100.0% (30/30 evaluated)" in at_report


def test_monthly_trend_cell_marks_a_thin_month(tmp_path):
    db = _seed(tmp_path / "learning.db", n_recs=20, n_eval=4, n_win=4)
    report = generate_monthly_report(db, months_back=3)

    assert "insufficient_sample (n=4)" in report
    assert "| 2026-07 |" in report


def test_cli_json_reports_the_gate(tmp_path, monkeypatch, capsys):
    db = _seed(tmp_path / "learning.db", n_recs=20, n_eval=4, n_win=4)
    monkeypatch.setattr(monthly_report, "get_db", lambda: db)

    assert monthly_report.main(as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["sample_status"] == "insufficient_sample"
    assert payload["evaluated_outcomes"] == 4
    assert payload["total_recommendations"] == 20
    assert payload["coverage_pct"] == pytest.approx(20.0)
    assert payload["success_rate_pct"] is None
    assert payload["min_closed_outcomes"] == 30
    assert payload["note"] == (
        "insufficient_sample (evaluated=4 of 20 recommendations, 20.00% coverage)"
    )
