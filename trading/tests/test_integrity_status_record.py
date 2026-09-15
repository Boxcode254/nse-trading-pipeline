"""TP-008 — current integrity status record vs the append-only incident log.

Fixture-only: everything is written under ``tmp_path``; the real
``portfolio/`` directory is never touched.
"""
from __future__ import annotations

import json
from datetime import datetime

from trading.services import integrity_status as istat

NOW = datetime.fromisoformat("2026-09-15T12:00:00+03:00")


def test_record_roundtrip_and_fields(tmp_path):
    rec = istat.record(
        tmp_path,
        status=istat.STATUS_OK,
        checked_at="2026-09-15T11:59:00+03:00",
        source_file="axys_closes_2026-09-14.json",
        source_date="2026-09-14",
        positions_checked=3,
        tolerance_pct=1.0,
    )

    on_disk = json.loads((tmp_path / istat.STATUS_FILENAME).read_text())
    assert on_disk == rec
    assert on_disk["status"] == "ok"
    assert on_disk["checked_at"] == "2026-09-15T11:59:00+03:00"
    assert on_disk["source_file"] == "axys_closes_2026-09-14.json"
    assert on_disk["source_date"] == "2026-09-14"
    assert on_disk["positions_checked"] == 3
    assert on_disk["tolerance_pct"] == 1.0
    assert on_disk["divergences"] == []
    assert on_disk["last_failure"] is None
    assert istat.load(tmp_path) == on_disk


def test_failure_sets_last_failure_and_ok_does_not_clear_it(tmp_path):
    istat.record(tmp_path, status=istat.STATUS_OK, checked_at="2026-09-01T19:35:00+03:00",
                 source_file="axys_closes_2026-09-01.json", source_date="2026-09-01")

    failed = istat.record(
        tmp_path,
        status=istat.STATUS_DIVERGENCES,
        checked_at="2026-09-02T19:35:00+03:00",
        source_file="axys_closes_2026-09-02.json",
        source_date="2026-09-02",
        positions_checked=2,
        divergences=[{"symbol": "TOTL", "book_price": 12.5, "official": 13.0, "delta_pct": -3.85}],
    )
    assert failed["status"] == "divergences"
    assert failed["last_failure"]["checked_at"] == "2026-09-02T19:35:00+03:00"
    assert failed["last_failure"]["status"] == "divergences"
    assert "TOTL" in failed["last_failure"]["summary"]

    recovered = istat.record(
        tmp_path, status=istat.STATUS_OK, checked_at="2026-09-15T19:35:00+03:00",
        source_file="axys_closes_2026-09-14.json", source_date="2026-09-14", positions_checked=3,
    )
    # current status is ok ...
    assert recovered["status"] == "ok" and recovered["divergences"] == []
    # ... but the incident is preserved as history, not erased and not current
    assert recovered["last_failure"]["checked_at"] == "2026-09-02T19:35:00+03:00"
    assert recovered["last_failure"]["divergences"][0]["symbol"] == "TOTL"


def test_renderer_separates_current_from_last_failure_with_computed_ages(tmp_path):
    istat.record(tmp_path, status=istat.STATUS_DIVERGENCES,
                 checked_at="2026-08-27T18:22:00+03:00",
                 source_file="axys_closes_2026-08-27.json", source_date="2026-08-27",
                 positions_checked=1,
                 divergences=[{"symbol": "TOTL", "book_price": 1.0, "official": 2.0, "delta_pct": -50.0}])
    istat.record(tmp_path, status=istat.STATUS_OK,
                 checked_at="2026-09-15T11:00:00+03:00",
                 source_file="axys_closes_2026-09-14.json", source_date="2026-09-14",
                 positions_checked=3)

    lines = istat.format_report_lines(istat.load(tmp_path), now=NOW)
    current, last_failure = lines[0], lines[-1]

    assert current.startswith("Current integrity: ok")
    assert "checked 2026-09-15T11:00" in current
    assert "1.0h ago" in current          # computed, not written down
    assert "source axys_closes_2026-09-14.json (2026-09-14)" in current
    assert "positions checked 3" in current

    assert last_failure.startswith("Last recorded failure: 2026-08-27T18:22")
    assert "18.7d ago" in last_failure
    assert "TOTL" in last_failure              # summary carried on the failure, not the status
    assert "vs axys_closes_2026-08-27.json" in last_failure
    # the stale incident is never presented as the current status
    assert "Current integrity: DIVERGENCES" not in lines[0]


def test_renderer_marks_stale_current_record(tmp_path):
    istat.record(tmp_path, status=istat.STATUS_OK, checked_at="2026-09-01T19:35:00+03:00",
                 positions_checked=2)
    lines = istat.format_report_lines(istat.load(tmp_path), now=NOW)
    assert "STALE (no run in >2.0d)" in lines[0]
    assert lines[-1] == "Last recorded failure: none recorded"


def test_renderer_handles_missing_and_corrupt_records(tmp_path):
    missing = istat.format_report_lines(None, now=NOW)
    assert "no status record on file" in missing[0]

    (tmp_path / istat.STATUS_FILENAME).write_text("{not json")
    assert istat.load(tmp_path) is None
    corrupt = istat.format_report_lines(istat.load(tmp_path), now=NOW)
    assert "no status record on file" in corrupt[0]


def test_no_source_and_error_statuses_are_recorded(tmp_path):
    nosource = istat.record(tmp_path, status=istat.STATUS_NO_SOURCE,
                            detail="no official AXYS close within 7d of 2026-09-15")
    assert nosource["status"] == "no_source"
    lines = istat.format_report_lines(nosource, now=NOW)
    assert "NO OFFICIAL SOURCE" in lines[0]

    errored = istat.record(tmp_path, status=istat.STATUS_ERROR, detail="cannot read book: boom")
    assert errored["status"] == "error"
    lines = istat.format_report_lines(errored, now=NOW)
    assert "ERROR" in lines[0]
    assert "cannot read book: boom" in "\n".join(lines)
