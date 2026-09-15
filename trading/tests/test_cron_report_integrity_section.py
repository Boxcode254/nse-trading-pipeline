"""TP-008 — the supervision report must show CURRENT integrity, not a stale log tail.

Exercises the real report module (``scripts/_cron_full_report.py``) with
fixture portfolios under ``tmp_path``; the production portfolio dir is never
read or written.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

from trading.services import integrity_status as istat

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.fromisoformat("2026-09-15T12:00:00+03:00")

_STALE_LOG = (
    "[2026-08-27 18:22:31] Book-integrity check -- 1 divergence(s) vs "
    "axys_closes_2026-08-27.json (2026-08-27)\n"
    "  TOTL: book=1.00 official=2.00 delta=-50.00%\n"
)


@pytest.fixture(scope="module")
def report_mod():
    path = REPO_ROOT / "scripts" / "_cron_full_report.py"
    spec = importlib.util.spec_from_file_location("cron_full_report_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_no_status_record_does_not_replay_the_incident_log(report_mod, tmp_path):
    """Regression: the log tail used to be printed as the current status."""
    (tmp_path / "book_integrity.log").write_text(_STALE_LOG)

    lines = report_mod.integrity_lines(tmp_path, now=NOW)
    text = "\n".join(lines)

    assert "no status record on file" in text
    assert "TOTL" not in text
    assert "2026-08-27" not in text
    assert "Book-integrity check" not in text


def test_current_status_is_rendered_with_computed_age(report_mod, tmp_path):
    (tmp_path / "book_integrity.log").write_text(_STALE_LOG)
    istat.record(tmp_path, status=istat.STATUS_OK,
                 checked_at="2026-09-15T11:00:00+03:00",
                 source_file="axys_closes_2026-09-14.json", source_date="2026-09-14",
                 positions_checked=3)

    lines = report_mod.integrity_lines(tmp_path, now=NOW)
    text = "\n".join(lines)

    assert lines[0].startswith("Current integrity: ok")
    assert "checked 2026-09-15T11:00" in lines[0]
    assert "1.0h ago" in lines[0]
    assert "positions checked 3" in lines[0]
    assert lines[-1] == "Last recorded failure: none recorded"
    # the ancient log entry is not shown as the current state
    assert "TOTL" not in text


def test_current_ok_with_recorded_historical_failure_is_distinguished(report_mod, tmp_path):
    (tmp_path / "book_integrity.log").write_text(_STALE_LOG)
    istat.record(tmp_path, status=istat.STATUS_DIVERGENCES,
                 checked_at="2026-08-27T18:22:31+03:00",
                 source_file="axys_closes_2026-08-27.json", source_date="2026-08-27",
                 positions_checked=1,
                 divergences=[{"symbol": "TOTL", "book_price": 1.0, "official": 2.0,
                               "delta_pct": -50.0}])
    istat.record(tmp_path, status=istat.STATUS_OK,
                 checked_at="2026-09-15T11:00:00+03:00",
                 source_file="axys_closes_2026-09-14.json", source_date="2026-09-14",
                 positions_checked=3)

    lines = report_mod.integrity_lines(tmp_path, now=NOW)

    assert lines[0].startswith("Current integrity: ok")
    assert "DIVERGENCES" not in lines[0]
    assert lines[-1].startswith("Last recorded failure: 2026-08-27T18:22")
    assert "18.7d ago" in lines[-1]
    assert "TOTL" in lines[-1]
    assert len(lines) == 2


def test_live_divergences_are_current_and_still_listed(report_mod, tmp_path):
    istat.record(tmp_path, status=istat.STATUS_DIVERGENCES,
                 checked_at="2026-09-15T11:59:00+03:00",
                 source_file="axys_closes_2026-09-14.json", source_date="2026-09-14",
                 positions_checked=2,
                 divergences=[{"symbol": "SCOM", "book_price": 30.5, "official": 29.0,
                               "delta_pct": 5.17}])

    lines = report_mod.integrity_lines(tmp_path, now=NOW)

    assert lines[0].startswith("Current integrity: DIVERGENCES")
    assert "SCOM" in lines[1]
    assert "delta=+5.17%" in lines[1]
    assert lines[-1].startswith("Last recorded failure: 2026-09-15T11:59")


def test_report_section_contains_no_hardcoded_dates_or_staleness(report_mod):
    """No fixed date/staleness string may survive in the touched paths."""
    source = (REPO_ROOT / "scripts" / "_cron_full_report.py").read_text()
    assert "book_integrity.log" not in source
    assert "integrity_status" in source
    for fixed in ("2d stale", "Cross-source reconciliation ran", "2026-08-27",
                  "benchmark snapshot is"):
        assert fixed not in source
