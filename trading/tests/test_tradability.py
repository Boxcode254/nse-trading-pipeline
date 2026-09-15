"""Deterministic fixture tests for the central tradability predicate.

TP-003 / TP-009. ``trading/tradability.py`` is the ONE source of truth for
"can we trade this?" — static suspension (``config.SUSPENDED_SYMBOLS``) plus
the dynamic OHLC-lock detector (``trading.risk.illiquidity_detector``).

Everything here is hermetic: pandas frames built in memory, no production
state (portfolio/, execution/, data/, logs/) is read or written.
"""
from __future__ import annotations

import pandas as pd
import pytest

from trading import config
from trading import tradability
from trading.tradability import (
    REASON_ILLIQUID,
    REASON_SUSPENDED,
    REASON_TRADABLE,
    eligible_pairs,
    filter_tradable,
    is_suspended,
    is_tradable,
    non_tradable,
    verdict,
)

SUSPENDED_SAMPLE = "BAMB"
TRADABLE_SAMPLE = "SCOM"


# ── fixtures ──────────────────────────────────────────────────────────────

def _bars(closes, *, lock: bool, start: str = "2026-01-01"):
    """Build an OHLCV frame; when ``lock`` every bar is O=H=L=C (suspended)."""
    rows = []
    dates = pd.date_range(start, periods=len(closes), freq="B")
    for d, c in zip(dates, closes):
        if lock:
            o = h = lo = float(c)
        else:
            o, h, lo = float(c) - 1.0, float(c) + 2.0, float(c) - 2.5
        rows.append({"date": d, "open": o, "high": h, "low": lo,
                     "close": float(c), "volume": 1500})
    return pd.DataFrame(rows)


@pytest.fixture
def locked_df():
    """>= HARD_LOCK_BARS (10) consecutive O=H=L=C bars — the suspension shape."""
    return _bars([54.0] * 27, lock=True)


@pytest.fixture
def healthy_df():
    return _bars([100 + i for i in range(20)], lock=False)


# ── preconditions ─────────────────────────────────────────────────────────

def test_static_suspended_universe_is_configured():
    """The static list this predicate reads must actually contain BAMB."""
    assert SUSPENDED_SAMPLE in config.SUSPENDED_SYMBOLS
    assert SUSPENDED_SAMPLE in config.PAIRS, "PAIRS must still list BAMB (excluded downstream)"


# ── static verdicts ───────────────────────────────────────────────────────

def test_static_suspended_verdict_is_non_tradable():
    v = verdict(SUSPENDED_SAMPLE)
    assert v.tradable is False
    assert v.status == "non_tradable"
    assert v.reason == REASON_SUSPENDED
    assert v.suspended is True
    assert v.symbol == SUSPENDED_SAMPLE
    assert v.detail  # an explanation is always attached


def test_static_tradable_verdict_for_ordinary_name():
    v = verdict(TRADABLE_SAMPLE)
    assert v.tradable is True
    assert v.status == "tradable"
    assert v.reason == REASON_TRADABLE
    assert v.suspended is False


def test_normalisation_lowercase_and_whitespace():
    assert verdict("  bamb ").tradable is False
    assert verdict("bamb").symbol == SUSPENDED_SAMPLE
    assert verdict("scom").tradable is True
    assert verdict("  Scom  ").symbol == TRADABLE_SAMPLE


def test_is_tradable_and_is_suspended_agree_with_verdict():
    assert is_tradable(TRADABLE_SAMPLE) is True
    assert is_tradable(SUSPENDED_SAMPLE) is False
    assert is_suspended(SUSPENDED_SAMPLE) is True
    assert is_suspended(TRADABLE_SAMPLE) is False


def test_verdict_as_dict_shape():
    d = verdict(SUSPENDED_SAMPLE).as_dict()
    assert d == {
        "symbol": SUSPENDED_SAMPLE,
        "tradable": False,
        "status": "non_tradable",
        "reason": REASON_SUSPENDED,
        "detail": tradability.SUSPENDED_DETAIL,
    }


# ── raw / None / empty inputs ─────────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, "", "   ", 0, [], {}])
def test_empty_or_none_inputs_never_raise(raw):
    """Garbage in must not raise — the predicate is a safe, total function.

    An unidentifiable symbol is reported as tradable (fail-open for *reading*;
    the execution gate is a separate layer) with an empty normalised symbol.
    """
    v = verdict(raw)
    assert v.symbol == ""
    assert v.tradable is True


def test_non_string_truthy_input_is_normalised():
    assert verdict(123).symbol == "123"
    assert verdict(["sc"]).symbol == "['SC']"  # documented str() fallback, never a crash


def test_none_and_empty_symbols_are_not_suspended():
    assert is_suspended(None) is False
    assert is_suspended("") is False


# ── dynamic OHLC-lock verdicts ────────────────────────────────────────────

def test_locked_frame_makes_tradable_name_illiquid(locked_df):
    v = verdict(TRADABLE_SAMPLE, locked_df)
    assert v.tradable is False
    assert v.status == "non_tradable"
    assert v.reason == REASON_ILLIQUID
    assert v.suspended is False, "illiquid is the dynamic reason, not static suspension"
    assert "lock" in v.detail.lower()


def test_healthy_frame_stays_tradable(healthy_df):
    v = verdict(TRADABLE_SAMPLE, healthy_df)
    assert v.tradable is True
    assert v.reason == REASON_TRADABLE


def test_static_suspension_wins_over_dynamic_data(locked_df, healthy_df):
    assert verdict(SUSPENDED_SAMPLE, locked_df).reason == REASON_SUSPENDED
    assert verdict(SUSPENDED_SAMPLE, healthy_df).reason == REASON_SUSPENDED


def test_empty_frame_falls_back_to_static_answer(healthy_df):
    empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    assert verdict(TRADABLE_SAMPLE, empty).tradable is True
    assert verdict(SUSPENDED_SAMPLE, empty).tradable is False


def test_unsized_frame_input_falls_back_to_static_answer():
    """A non-sized object must not blow up the dynamic check."""
    assert verdict(TRADABLE_SAMPLE, object()).tradable is True


def test_soft_lock_below_hard_threshold_stays_tradable():
    """5-9 locked bars is 'suspicious', not 'locked' — still tradable."""
    soft = _bars([54.0] * 6, lock=True)
    v = verdict(TRADABLE_SAMPLE, soft)
    assert v.tradable is True
    assert v.reason == REASON_TRADABLE


# ── filter_tradable ordering ──────────────────────────────────────────────

def test_filter_tradable_preserves_order_and_drops_suspended():
    syms = ["KCB", SUSPENDED_SAMPLE, "SCOM", "EQTY"]
    assert filter_tradable(syms) == ["KCB", "SCOM", "EQTY"]


def test_filter_tradable_applies_dynamic_lock_from_bars(locked_df, healthy_df):
    syms = ["SCOM", "KCB", "EQTY"]
    bars = {"SCOM": healthy_df, "KCB": locked_df, "EQTY": healthy_df}
    assert filter_tradable(syms, bars) == ["SCOM", "EQTY"]


def test_filter_tradable_empty_input():
    assert filter_tradable([]) == []


def test_non_tradable_reports_reasons(locked_df):
    out = non_tradable(["SCOM", SUSPENDED_SAMPLE, "KCB"], {"KCB": locked_df})
    reasons = {v.symbol: v.reason for v in out}
    assert reasons == {SUSPENDED_SAMPLE: REASON_SUSPENDED, "KCB": REASON_ILLIQUID}


# ── eligible universe ─────────────────────────────────────────────────────

def test_eligible_pairs_excludes_suspended_symbols():
    eligible = eligible_pairs()
    assert SUSPENDED_SAMPLE not in eligible
    assert SUSPENDED_SAMPLE in config.PAIRS, "precondition: PAIRS still lists BAMB"
    assert set(eligible) == set(config.PAIRS) - tradability.suspended_symbols()


def test_eligible_pairs_preserves_config_pair_order():
    eligible = eligible_pairs()
    expected = [p for p in config.PAIRS if p not in tradability.suspended_symbols()]
    assert eligible == expected
    assert TRADABLE_SAMPLE in eligible


def test_eligible_pairs_honours_explicit_input():
    assert eligible_pairs([SUSPENDED_SAMPLE, "SCOM"]) == ["SCOM"]
    assert eligible_pairs(["SCOM", SUSPENDED_SAMPLE, "KCB"]) == ["SCOM", "KCB"]


def test_suspended_symbols_are_normalised():
    assert config.SUSPENDED_SYMBOLS
    assert all(s == s.upper() for s in tradability.suspended_symbols())
