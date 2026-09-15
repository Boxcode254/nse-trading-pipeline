"""TP-006 — ``trading stats`` must not report a signal mix as a win rate.

Deterministic, fixture-only: every path is pointed at a tmp directory, no
production file or database is read or written.
"""
from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from trading import config
from trading.cli.commands import stats as stats_cmd
from trading.services import stats as stats_svc


def _write_signals(path, buy: int, sell: int, hold: int) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "pair", "signal", "price", "sma_fast", "sma_slow", "rsi"])
        for i in range(buy):
            w.writerow(["2026-09-%02d 09:00:00" % (i + 1), "SCOM", "BUY", 30.0, 1, 2, 50])
        for i in range(sell):
            w.writerow(["2026-09-%02d 09:00:00" % (i + 1), "KCB", "SELL", 45.0, 1, 2, 50])
        for i in range(hold):
            w.writerow(["2026-09-%02d 09:00:00" % (i + 1), "EQTY", "HOLD", 60.0, 1, 2, 50])


def _make_learning_db(path, *, n_buy=6, n_sell=2, n_hold=2, n_eval=0, n_win=0) -> str:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, date TEXT,
            confidence REAL, recommendation TEXT, score REAL, factors_hash TEXT,
            timestamp TEXT, created_at TEXT);
        CREATE TABLE outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, date TEXT,
            market_outcome TEXT, expected_return REAL, actual_return REAL,
            time_to_target INTEGER, success BOOLEAN, evaluated_at TEXT, created_at TEXT);
        """
    )
    recs = ["BUY"] * n_buy + ["SELL"] * n_sell + ["HOLD"] * n_hold
    for i, rec in enumerate(recs):
        conn.execute(
            "INSERT INTO recommendations (symbol, date, confidence, recommendation, score,"
            " factors_hash, timestamp, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("SYM%d" % i, "2026-07-%02d" % (i + 1), 0.6, rec, 50.0, "h%d" % i,
             "2026-07-01T00:00:00", "2026-07-01T00:00:00"),
        )
    for i in range(n_eval):
        conn.execute(
            "INSERT INTO outcomes (symbol, date, market_outcome, expected_return, actual_return,"
            " time_to_target, success, evaluated_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("SYM%d" % i, "2026-07-%02d" % (i + 1), "UP", 5.0, 4.0, 10,
             1 if i < n_win else 0, "2026-08-01T00:00:00", "2026-08-01T00:00:00"),
        )
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Point the signal/log reads at tmp fixtures."""
    sig = tmp_path / "signals.csv"
    _write_signals(sig, buy=4, sell=2, hold=4)
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(config, "SIGNALS_CSV", str(sig))
    monkeypatch.setattr(config, "LOGS_DIR", str(logs))
    return tmp_path


def test_signal_mix_is_reported_as_mix_not_win_rate(isolated, tmp_path):
    db = _make_learning_db(tmp_path / "learning.db", n_eval=0)
    result = stats_svc.build(db_path=db)

    # The old misleading names are gone, the real semantics are present.
    assert "win_rate_pct" not in result
    assert "actionable_win_rate_pct" not in result
    assert "avg_confidence" not in result
    assert result["buy_signal_share_pct"] == pytest.approx(40.0)          # 4 of 10 signals
    assert result["buy_share_of_actionable_pct"] == pytest.approx(66.67)  # 4 of 6 actionable


def test_insufficient_outcomes_publish_no_win_rate(isolated, tmp_path):
    db = _make_learning_db(tmp_path / "learning.db", n_buy=6, n_sell=2, n_hold=2,
                           n_eval=4, n_win=4)
    perf = stats_svc.build(db_path=db)["outcome_performance"]

    assert perf["win_rate_pct"] is None
    assert perf["sample_status"] == "insufficient_sample"
    assert perf["numerator"] == 4 and perf["denominator"] == 4
    assert perf["evaluated_outcomes"] == 4
    assert perf["recommendations_total"] == 10
    assert perf["eligible_sample"] == 8
    assert perf["unresolved_sample"] == 4
    assert perf["coverage_pct"] == pytest.approx(40.0)
    assert perf["min_evaluated_outcomes"] == 30
    assert "insufficient_sample (evaluated=4 of 10 recommendations, 40.00% coverage)" == perf["caveat"]
    assert "confidence" in perf["confidence_caveat"]


def test_sufficient_outcomes_publish_win_rate_with_ratio(isolated, tmp_path):
    db = _make_learning_db(tmp_path / "learning.db", n_buy=20, n_sell=20, n_hold=0,
                           n_eval=40, n_win=26)
    result = stats_svc.build(db_path=db)
    perf = result["outcome_performance"]

    assert perf["sample_status"] == "sufficient"
    assert perf["win_rate_pct"] == pytest.approx(65.0)
    assert perf["numerator"] == 26 and perf["denominator"] == 40
    assert perf["losses"] == 14
    assert perf["coverage_pct"] == pytest.approx(100.0)


def test_no_outcome_store_is_unavailable_not_zero(isolated, tmp_path):
    result = stats_svc.build(db_path=str(tmp_path / "missing.db"))
    perf = result["outcome_performance"]

    assert perf["available"] is False
    assert perf["sample_status"] == "unavailable"
    assert perf["win_rate_pct"] is None
    assert "unavailable" in result["best_strategy"]


def test_best_strategy_is_never_hardcoded(isolated, tmp_path):
    insufficient = _make_learning_db(tmp_path / "a.db", n_eval=2, n_win=2)
    assert stats_svc.build(db_path=insufficient)["best_strategy"] == (
        "unavailable: insufficient evaluated outcomes"
    )

    sufficient = _make_learning_db(tmp_path / "b.db", n_eval=30, n_win=20)
    best = stats_svc.build(db_path=sufficient)["best_strategy"]
    assert best.startswith("unavailable:")
    assert best != "A"
    assert "SMA(20/50)" not in best


def test_threshold_matches_monthly_report_constant():
    from learning import monthly_report

    assert stats_svc.MIN_EVALUATED_OUTCOMES == 30
    assert monthly_report.MIN_CLOSED_OUTCOMES == stats_svc.MIN_EVALUATED_OUTCOMES


def test_cli_json_carries_the_sample_gate(isolated, tmp_path, monkeypatch, capsys):
    db = _make_learning_db(tmp_path / "learning.db", n_eval=4, n_win=4)
    monkeypatch.setattr(stats_svc, "LEARNING_DB_PATH", db)

    assert stats_cmd.run(as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "win_rate_pct" not in payload
    assert "avg_confidence" not in payload
    assert payload["outcome_performance"]["sample_status"] == "insufficient_sample"
    assert payload["outcome_performance"]["win_rate_pct"] is None
    assert payload["best_strategy"] == "unavailable: insufficient evaluated outcomes"


def test_cli_quiet_and_table_do_not_claim_a_win_rate(isolated, tmp_path, monkeypatch, capsys):
    db = _make_learning_db(tmp_path / "learning.db", n_eval=4, n_win=4)
    monkeypatch.setattr(stats_svc, "LEARNING_DB_PATH", db)

    assert stats_cmd.run(quiet=True) == 0
    quiet = capsys.readouterr().out
    assert "buy_share=40.0%" in quiet
    assert "sample=insufficient_sample" in quiet
    assert "win%" not in quiet

    assert stats_cmd.run() == 0
    # rich wraps at the detected width — compare on flattened whitespace
    table = " ".join(capsys.readouterr().out.split())
    assert "Outcome win rate:" in table and "insufficient_sample" in table
    assert "Best strategy:" in table and "unavailable" in table
    assert "not a win rate" in table
