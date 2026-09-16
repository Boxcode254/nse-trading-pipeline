"""Task 7 (2026-09-15) — risk-input hygiene: momentum member coverage + services tier.

Item 1 — momentum-gate member availability. Previously ONE absent member CSV
(BRIT, not even held) marked the whole insurance sector ``data_unusable``, so a
held sector could not earn its momentum uplift on evidence that did exist.
Absent members are now SKIPPED (recorded with state ``missing``) and the uplift
is computed from the remaining members, but only when at least
``config.MOMENTUM_MIN_VALID_MEMBERS`` members have valid fresh data
(evidence-quality gate; fewer => reason ``insufficient_member_coverage``).
Malformed/stale members block only themselves.

Item 2 — an explicit ``services`` tier (WTK, a held orphan) equal to "other",
instead of falling through to the flat max_sector_exposure_pct default.

No threshold changed: base WARN/HARD caps, uplift size, lookback and
momentum_min_pct are asserted unchanged below (freeze guard).

Deterministic and production-state free: every price CSV is written into
``tmp_path`` and ``now`` is pinned, so nothing here reads the live ``data/``
cache, the clock or the portfolio.
"""
import csv
import os
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading import config  # noqa: E402

TODAY = date(2026, 9, 15)
GATE = config.EXECUTION_CONFIG["momentum_gate"]
SECTOR_CAPS = config.EXECUTION_CONFIG["sector_caps"]
UPLIFT = GATE["hard_uplift_pct"]
LOOKBACK = GATE["lookback_days"]
MAX_AGE = config.MOMENTUM_MAX_STALENESS_DAYS
MIN_VALID = config.MOMENTUM_MIN_VALID_MEMBERS


def members_of(sector):
    return [s for s, sec in config.SECTOR_MAP.items() if sec == sector]


BANKING = members_of("banking")
INSURANCE = members_of("insurance")


def _sessions(count, end):
    out, cursor = [], end
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(out))


def _write_csv(path, closes, last_session):
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for session, close in zip(_sessions(len(closes), last_session), closes):
            writer.writerow([session.isoformat(), close, close, close, close, 1000])


def _rising(n):
    return [round(100.0 * (1.005 ** i), 4) for i in range(n)]


def _falling(n):
    return [round(100.0 * (0.995 ** i), 4) for i in range(n)]


def _seed(data_dir, symbols, closes, last_session=TODAY):
    for sym in symbols:
        _write_csv(data_dir / f"nse_{sym}.csv", closes, last_session)


def _states(diag):
    return {m["symbol"]: m["state"] for m in diag["members"]}


def _counted(diag):
    return sorted(m["symbol"] for m in diag["members"] if m["counted"])


# ── Item 2 — services tier ────────────────────────────────────────────────
def test_services_tier_is_explicit_and_matches_other():
    assert SECTOR_CAPS["services"] == {"warn": 15.0, "hard": 20.0}
    assert SECTOR_CAPS["services"] == SECTOR_CAPS["other"]


def test_services_tier_is_tighter_than_the_flat_default():
    default = config.EXECUTION_CONFIG["max_sector_exposure_pct"]
    assert SECTOR_CAPS["services"]["warn"] < default
    assert SECTOR_CAPS["services"]["hard"] < default


def test_other_sector_tiers_unchanged():
    assert SECTOR_CAPS["banking"] == {"warn": 40.0, "hard": 45.0}
    assert SECTOR_CAPS["telecom"] == {"warn": 25.0, "hard": 30.0}
    assert SECTOR_CAPS["energy"] == {"warn": 25.0, "hard": 30.0}
    assert SECTOR_CAPS["insurance"] == {"warn": 20.0, "hard": 25.0}
    assert SECTOR_CAPS["consumer"] == {"warn": 20.0, "hard": 25.0}
    assert SECTOR_CAPS["other"] == {"warn": 15.0, "hard": 20.0}


def test_services_cap_applies_without_evidence(tmp_path):
    """WTK's sector resolves to the new tier even with no price evidence."""
    assert config.sector_cap("services", data_dir=tmp_path, now=TODAY) == {
        "warn": 15.0, "hard": 20.0,
    }


# ── Item 1 — coverage gate constants / freeze guard ───────────────────────
def test_min_valid_members_constant_is_two():
    assert MIN_VALID == 2


def test_momentum_thresholds_unchanged():
    assert GATE["enabled"] is True
    assert GATE["lookback_days"] == 20
    assert GATE["momentum_min_pct"] == 0.0
    assert GATE["hard_uplift_pct"] == 10.0
    assert MAX_AGE == 7


# ── Item 1 — availability behaviour ───────────────────────────────────────
def test_absent_member_is_skipped_and_remaining_members_grant_uplift(tmp_path):
    """The BRIT case: one absent CSV no longer disables a whole sector."""
    absent = BANKING[0]
    present = BANKING[1:]
    _seed(tmp_path, present, _rising(LOOKBACK + 5))

    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)

    assert diag["applied"] is True
    assert diag["reason"] == "uplift_applied"
    assert diag["blocking"] == []
    assert diag["valid_members"] == len(present) >= MIN_VALID
    assert diag["min_valid_members"] == MIN_VALID
    assert diag["skipped_members"] == [absent]
    assert diag["skipped_states"] == ["missing"]
    assert _states(diag)[absent] == "missing"
    assert absent not in _counted(diag)
    assert _counted(diag) == sorted(present)
    assert diag["effective_hard_uplift"] == UPLIFT

    base = SECTOR_CAPS["banking"]
    assert config.sector_cap("banking", data_dir=tmp_path, now=TODAY) == {
        "warn": base["warn"], "hard": base["hard"] + UPLIFT,
    }


def test_absent_member_detail_is_operator_readable(tmp_path):
    _seed(tmp_path, BANKING[1:], _rising(LOOKBACK + 5))
    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)
    entry = [m for m in diag["members"] if m["symbol"] == BANKING[0]][0]
    assert entry["state"] == "missing"
    assert entry["detail"] == "no cached CSV for this symbol"
    assert entry["counted"] is False
    assert entry["path"].endswith(f"nse_{BANKING[0]}.csv")


def test_single_valid_member_is_insufficient_coverage(tmp_path):
    """One fresh CSV is a single print, not a sector trend: no uplift, honestly."""
    _seed(tmp_path, BANKING[:1], _rising(LOOKBACK + 5))

    with pytest.warns(RuntimeWarning, match="momentum uplift disabled"):
        cap = config.sector_cap("banking", data_dir=tmp_path, now=TODAY)
    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)

    base = SECTOR_CAPS["banking"]
    assert cap == {"warn": base["warn"], "hard": base["hard"]}
    assert diag["applied"] is False
    assert diag["reason"] == "insufficient_member_coverage"
    assert diag["blocking"] == ["insufficient_member_coverage"]
    assert diag["valid_members"] == 1
    assert diag["effective_hard_uplift"] == 0.0
    assert "1 member(s) with valid fresh price data" in diag["detail"]
    assert "min 2" in diag["detail"]
    assert _states(diag)[BANKING[0]] == "ok"  # the one member we do have is usable


def test_zero_members_present_is_insufficient_coverage(tmp_path):
    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)
    assert diag["reason"] == "insufficient_member_coverage"
    assert diag["valid_members"] == 0
    assert diag["skipped_members"] == BANKING
    assert set(diag["skipped_states"]) == {"missing"}
    assert config.sector_cap("banking", data_dir=tmp_path, now=TODAY) == dict(
        warn=SECTOR_CAPS["banking"]["warn"], hard=SECTOR_CAPS["banking"]["hard"]
    )


def test_malformed_member_is_skipped_not_sector_fatal(tmp_path):
    _seed(tmp_path, BANKING, _rising(LOOKBACK + 5))
    broken = tmp_path / f"nse_{BANKING[0]}.csv"
    with broken.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "notes"])
        writer.writerow([TODAY.isoformat(), "no price here"])

    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)

    assert _states(diag)[BANKING[0]] == "malformed"
    assert BANKING[0] in diag["skipped_members"]
    assert BANKING[0] not in _counted(diag)
    assert diag["applied"] is True
    assert diag["reason"] == "uplift_applied"
    assert diag["valid_members"] == len(BANKING) - 1


def test_stale_members_are_skipped_not_sector_fatal(tmp_path):
    _seed(tmp_path, BANKING, _rising(LOOKBACK + 5), TODAY - timedelta(days=MAX_AGE + 23))
    fresh = BANKING[:MIN_VALID]
    _seed(tmp_path, fresh, _rising(LOOKBACK + 5), TODAY)

    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)

    assert diag["applied"] is True
    assert diag["valid_members"] == MIN_VALID
    assert _counted(diag) == sorted(fresh)
    assert sorted(diag["skipped_members"]) == sorted(BANKING[MIN_VALID:])
    assert all(_states(diag)[s] == "stale" for s in BANKING[MIN_VALID:])
    assert all(
        m["staleness_days"] > MAX_AGE for m in diag["members"] if m["symbol"] in BANKING[MIN_VALID:]
    )


def test_future_dated_member_is_skipped_not_sector_fatal(tmp_path):
    _seed(tmp_path, BANKING, _rising(LOOKBACK + 5))
    _seed(tmp_path, BANKING[:1], _rising(LOOKBACK + 5), TODAY + timedelta(days=5))

    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)
    assert _states(diag)[BANKING[0]] == "malformed"
    assert BANKING[0] in diag["skipped_members"]
    assert diag["applied"] is True


def test_single_rising_member_cannot_grant_uplift_alone(tmp_path):
    """The coverage gate holds even when the one usable member is strongly rising."""
    _seed(tmp_path, BANKING[:1], _rising(LOOKBACK + 5))

    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)
    assert diag["valid_members"] == 1
    assert diag["applied"] is False
    assert diag["reason"] == "insufficient_member_coverage"
    assert diag["effective_hard_uplift"] == 0.0


def test_momentum_below_threshold_still_uses_remaining_members(tmp_path):
    _seed(tmp_path, BANKING[1:], _rising(LOOKBACK + 5))
    _seed(tmp_path, BANKING[:1], _falling(LOOKBACK + 5))
    diag = config.sector_momentum_diagnostics("banking", data_dir=tmp_path, now=TODAY)
    assert diag["valid_members"] == len(BANKING)
    assert diag["skipped_members"] == []


# ── Item 1 — the insurance case, honestly ─────────────────────────────────
def test_insurance_with_brit_absent_reports_insufficient_coverage(tmp_path):
    """Insurance has only KNRE + BRIT: with BRIT absent, coverage is 1 of 2.

    The sector is no longer ``data_unusable`` (KNRE's evidence is inspected and
    reported), but it still cannot earn the uplift until a second member has
    valid fresh data — an absent member is skipped, it does not count as
    evidence. Recorded here so the residual gap is visible, not assumed fixed.
    """
    assert set(INSURANCE) == {"KNRE", "BRIT"}
    _seed(tmp_path, ["KNRE"], _rising(LOOKBACK + 5))

    diag = config.sector_momentum_diagnostics("insurance", data_dir=tmp_path, now=TODAY)

    assert diag["applied"] is False
    assert diag["reason"] == "insufficient_member_coverage"
    assert diag["valid_members"] == 1
    assert diag["skipped_members"] == ["BRIT"]
    assert _states(diag) == {"KNRE": "ok", "BRIT": "missing"}
    assert config.sector_cap("insurance", data_dir=tmp_path, now=TODAY) == {
        "warn": SECTOR_CAPS["insurance"]["warn"], "hard": SECTOR_CAPS["insurance"]["hard"],
    }


def test_insurance_earns_uplift_once_both_members_have_fresh_data(tmp_path):
    _seed(tmp_path, INSURANCE, _rising(LOOKBACK + 5))
    diag = config.sector_momentum_diagnostics("insurance", data_dir=tmp_path, now=TODAY)
    assert diag["applied"] is True
    assert diag["valid_members"] == 2
    assert diag["skipped_members"] == []
    assert config.sector_cap("insurance", data_dir=tmp_path, now=TODAY) == {
        "warn": 20.0, "hard": 25.0 + UPLIFT,
    }
