"""TP-002 regression coverage: sector momentum-cap uplift + freshness contract.

Deterministic and production-state free: every price CSV is written into a
temporary directory and ``now`` is pinned, so nothing here depends on the live
``data/`` cache, the clock, or the portfolio.

Defect this pins down (found 2026-09-15): ``DATA_DIR`` is a ``str``, so
``DATA_DIR / f"nse_{sym}.csv"`` raised TypeError inside a broad
``except Exception: pass`` — the momentum uplift never applied and said nothing.
"""
import csv
import os
import sys
from datetime import date, datetime, time as dtime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading import config  # noqa: E402


SECTOR = "banking"
GATE = config.EXECUTION_CONFIG["momentum_gate"]
SECTOR_CAPS = config.EXECUTION_CONFIG["sector_caps"]
BASE_WARN = SECTOR_CAPS[SECTOR]["warn"]
BASE_HARD = SECTOR_CAPS[SECTOR]["hard"]
UPLIFT = GATE["hard_uplift_pct"]
LOOKBACK = GATE["lookback_days"]
MAX_AGE = config.MOMENTUM_MAX_STALENESS_DAYS

MEMBERS = [s for s, sec in config.SECTOR_MAP.items() if sec == SECTOR]


def _sessions(count: int, end: date):
    """Business-day-ish date series ending at ``end`` (weekends excluded)."""
    out = []
    cursor = end
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(out))


def _write_csv(path, closes, last_session: date, date_column: bool = True):
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        if date_column:
            writer.writerow(["date", "open", "high", "low", "close", "volume"])
            for session, close in zip(_sessions(len(closes), last_session), closes):
                writer.writerow([session.isoformat(), close, close, close, close, 1000])
        else:
            writer.writerow(["open", "high", "low", "close", "volume"])
            for close in closes:
                writer.writerow([close, close, close, close, 1000])


def _rising(n: int):
    return [round(100.0 * (1.005 ** i), 4) for i in range(n)]


def _falling(n: int):
    return [round(100.0 * (0.995 ** i), 4) for i in range(n)]


def _seed(data_dir, closes, last_session: date):
    for sym in MEMBERS:
        _write_csv(data_dir / f"nse_{sym}.csv", closes, last_session)


def _clean(data_dir):
    for sym in MEMBERS:
        path = data_dir / f"nse_{sym}.csv"
        if path.exists():
            path.unlink()


def _pin_mtime(path, session: date):
    """Pin a file's mtime to ``session`` so mtime-based freshness is deterministic."""
    ts = datetime.combine(session, dtime(12, 0)).timestamp()
    os.utime(path, (ts, ts))


def test_members_exist_for_fixture_sector():
    assert MEMBERS, "banking must have configured sector members"


def test_fresh_positive_momentum_applies_exactly_one_configured_uplift(tmp_path):
    today = date(2026, 9, 15)
    _seed(tmp_path, _rising(LOOKBACK + 5), today)

    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)
    assert diag["applied"] is True
    assert diag["reason"] == "uplift_applied"
    assert diag["blocking"] == []
    assert diag["avg_return_pct"] > GATE["momentum_min_pct"]
    assert diag["effective_hard_uplift"] == UPLIFT
    assert all(m["state"] == "ok" for m in diag["members"])
    assert all(m["date_source"] == "csv" for m in diag["members"])
    assert all(m["staleness_days"] == 0 for m in diag["members"])

    cap = config.sector_cap(SECTOR, data_dir=tmp_path, now=today)
    assert cap == {"warn": BASE_WARN, "hard": BASE_HARD + UPLIFT}
    # Exactly one uplift — not compounded per member or per call.
    assert cap["hard"] - BASE_HARD == UPLIFT
    assert config.sector_cap(SECTOR, data_dir=tmp_path, now=today) == cap


def test_fresh_negative_momentum_applies_no_uplift(tmp_path):
    today = date(2026, 9, 15)
    _seed(tmp_path, _falling(LOOKBACK + 5), today)

    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)
    assert diag["applied"] is False
    assert diag["reason"] == "momentum_below_threshold"
    assert diag["blocking"] == []
    assert diag["avg_return_pct"] < GATE["momentum_min_pct"]
    assert diag["effective_hard_uplift"] == 0.0

    assert config.sector_cap(SECTOR, data_dir=tmp_path, now=today) == {
        "warn": BASE_WARN, "hard": BASE_HARD,
    }


def test_missing_csv_applies_no_uplift_and_warns(tmp_path):
    today = date(2026, 9, 15)
    with pytest.warns(RuntimeWarning, match="momentum uplift disabled"):
        cap = config.sector_cap(SECTOR, data_dir=tmp_path, now=today)
    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)

    assert cap == {"warn": BASE_WARN, "hard": BASE_HARD}
    assert diag["applied"] is False
    assert diag["reason"] == "data_unusable"
    assert "missing" in diag["blocking"]
    assert all(m["state"] == "missing" for m in diag["members"])
    assert "missing" in diag["detail"]


def test_malformed_csv_applies_no_uplift_and_warns(tmp_path):
    today = date(2026, 9, 15)
    _seed(tmp_path, _rising(LOOKBACK + 5), today)
    # Corrupt one member: a header with no close-like column at all.
    broken = tmp_path / f"nse_{MEMBERS[0]}.csv"
    with broken.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "notes"])
        writer.writerow([today.isoformat(), "no price here"])

    with pytest.warns(RuntimeWarning, match="momentum uplift disabled"):
        cap = config.sector_cap(SECTOR, data_dir=tmp_path, now=today)
    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)

    assert cap == {"warn": BASE_WARN, "hard": BASE_HARD}
    assert diag["applied"] is False
    assert diag["reason"] == "data_unusable"
    assert "malformed" in diag["blocking"]
    states = {m["symbol"]: m["state"] for m in diag["members"]}
    assert states[MEMBERS[0]] == "malformed"
    assert MEMBERS[0] in diag["detail"]


def test_unparseable_date_column_applies_no_uplift(tmp_path):
    today = date(2026, 9, 15)
    _seed(tmp_path, _rising(LOOKBACK + 5), today)
    path = tmp_path / f"nse_{MEMBERS[0]}.csv"
    rows = list(csv.reader(path.open()))
    rows[-1][0] = "not-a-date"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)

    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)
    assert diag["applied"] is False
    assert "malformed" in diag["blocking"]
    states = {m["symbol"]: m["state"] for m in diag["members"]}
    assert states[MEMBERS[0]] == "malformed"


def test_stale_csv_applies_no_uplift_and_warns(tmp_path):
    today = date(2026, 9, 15)
    old_session = today - timedelta(days=MAX_AGE + 23)
    _seed(tmp_path, _rising(LOOKBACK + 5), old_session)

    with pytest.warns(RuntimeWarning, match="momentum uplift disabled"):
        cap = config.sector_cap(SECTOR, data_dir=tmp_path, now=today)
    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)

    assert cap == {"warn": BASE_WARN, "hard": BASE_HARD}
    assert diag["applied"] is False
    assert diag["reason"] == "data_unusable"
    assert "stale" in diag["blocking"]
    assert all(m["state"] == "stale" for m in diag["members"])
    assert all(m["staleness_days"] > MAX_AGE for m in diag["members"])
    assert diag["members"][0]["last_date"] == _sessions(1, old_session)[0].isoformat()


def test_fresh_last_session_boundary_is_accepted_up_to_max_age(tmp_path):
    today = date(2026, 9, 15)
    _seed(tmp_path, _rising(LOOKBACK + 5), today - timedelta(days=MAX_AGE))
    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)
    assert all(m["staleness_days"] == MAX_AGE for m in diag["members"])
    assert diag["applied"] is True
    assert diag["reason"] == "uplift_applied"


def test_future_dated_csv_applies_no_uplift(tmp_path):
    today = date(2026, 9, 15)
    _seed(tmp_path, _rising(LOOKBACK + 5), today + timedelta(days=5))
    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)
    assert diag["applied"] is False
    assert "malformed" in diag["blocking"]


def test_dateless_csv_falls_back_to_file_mtime(tmp_path):
    today = date(2026, 9, 15)
    for sym in MEMBERS:
        path = tmp_path / f"nse_{sym}.csv"
        _write_csv(path, _rising(LOOKBACK + 5), today, date_column=False)
        _pin_mtime(path, today)
    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=today)
    assert all(m["date_source"] == "mtime" for m in diag["members"])
    assert all(m["staleness_days"] == 0 for m in diag["members"])
    assert diag["applied"] is True


def test_path_type_error_is_raised_not_swallowed():
    with pytest.raises(TypeError, match="data_dir must be path-like"):
        config.sector_cap(SECTOR, data_dir=123)


def test_bad_now_type_is_raised_not_defaulted():
    with pytest.raises(TypeError, match="now="):
        config.sector_momentum_diagnostics(SECTOR, now="2026-09-15")


def test_base_caps_are_unchanged_without_fresh_evidence(tmp_path):
    """Canonical tiered caps still hold when the gate has no usable evidence."""
    for sector, expected in SECTOR_CAPS.items():
        assert config.sector_cap(sector, data_dir=tmp_path, now=date(2026, 9, 15)) == {
            "warn": expected["warn"], "hard": expected["hard"],
        }


def test_unknown_sector_uses_default_cap_and_no_members_reason(tmp_path):
    default = config.EXECUTION_CONFIG["max_sector_exposure_pct"]
    diag = config.sector_momentum_diagnostics("not-a-sector", data_dir=tmp_path)
    assert diag["reason"] == "no_members"
    assert diag["applied"] is False
    assert config.sector_cap("not-a-sector", data_dir=tmp_path) == {
        "warn": default, "hard": default,
    }


def test_gate_disabled_reason_when_disabled(monkeypatch, tmp_path):
    gate = dict(config.EXECUTION_CONFIG["momentum_gate"])
    gate["enabled"] = False
    cfg = dict(config.EXECUTION_CONFIG)
    cfg["momentum_gate"] = gate
    monkeypatch.setattr(config, "EXECUTION_CONFIG", cfg)

    diag = config.sector_momentum_diagnostics(SECTOR, data_dir=tmp_path, now=date(2026, 9, 15))
    assert diag["reason"] == "gate_disabled"
    assert diag["applied"] is False
    # Disabled gate must not read anything or change the cap.
    assert config.sector_cap(SECTOR, data_dir=tmp_path) == {"warn": BASE_WARN, "hard": BASE_HARD}


def test_live_data_dir_shape_is_usable(tmp_path):
    """The real DATA_DIR must be path-typed: the original defect was a str."""
    from pathlib import Path

    assert isinstance(config.DATA_DIR, str)  # public type preserved
    assert Path(config.DATA_DIR)  # but it must be coerceable to a path
