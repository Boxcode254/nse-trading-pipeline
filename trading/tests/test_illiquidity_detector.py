"""Enforce the illiquidity detector's calibrated behaviour.

These tests guard the SILENT-FREEZE fix: a statically-unlisted suspended
name must be caught from price data alone, and naturally-thin names must
NOT trip the threshold (avoiding the false-positive noise Kratos flagged).

Run: pytest trading/tests/test_illiquidity_detector.py
"""
from __future__ import annotations

import pandas as pd
import pytest

from trading.risk.illiquidity_detector import (
    detect_illiquidity,
    scan_universe,
    HARD_LOCK_BARS,
    SOFT_LOCK_BARS,
)


def _df(prices, vols=None, dates=None, locks=None):
    """Build a bar frame. `locks[i]=True` forces open=high=low=close=prices[i]
    (a frozen bar). Otherwise the bar gets a realistic intraday range so it is
    NOT counted as locked. This mirrors real NSE data where active names have
    O!=H!=L!=C on normal days (even thin ones like WTK)."""
    n = len(prices)
    if dates is None:
        dates = pd.date_range("2025-01-01", periods=n, freq="B")
    if vols is None:
        vols = [1000] * n
    if locks is None:
        locks = [False] * n
    rows = []
    for i, (px, vol, lk) in enumerate(zip(prices, vols, locks)):
        if lk:
            o = h = l = c = px
        else:
            # realistic intraday range around the close
            o = round(px * 0.99, 2)
            h = round(px * 1.02, 2)
            l = round(px * 0.98, 2)
            c = px
        rows.append({"date": dates[i], "open": o, "high": h, "low": l,
                     "close": c, "volume": vol})
    return pd.DataFrame(rows)


def test_bamb_real_cache_locks():
    """BAMB's calibrated 27-bar OHLC lock is reported as locked."""
    df = _df([10.0] * (HARD_LOCK_BARS + 5), locks=[True] * (HARD_LOCK_BARS + 5))
    v = detect_illiquidity("BAMB", df)
    assert v.status == "locked"
    assert v.run_length >= HARD_LOCK_BARS


def test_natural_illiquidity_does_not_false_positive():
    """Thin names (WTK max lock=7) stay below the hard threshold."""
    # Simulate a 7-bar lock then normal trading resumes — must stay healthy.
    prices = [10.0] * 7 + [10.5, 9.8, 11.2, 10.9, 10.1]
    locks = [True] * 7 + [False] * 5
    df = _df(prices, locks=locks)
    v = detect_illiquidity("WTK_SIM", df)
    assert v.status == "healthy", f"expected healthy, got {v.status} ({v.run_length})"


def test_ohlc_lock_fires_on_unlisted_suspended_name():
    """The core fix: a name NOT in config.SUSPENDED_SYMBOLS but frozen
    must be detected. This is what prevents silent capital freeze."""
    # 15 consecutive fully-locked bars (simulating a just-suspended counter)
    prices = [10.0] * 15
    locks = [True] * 15
    df = _df(prices, locks=locks)
    v = detect_illiquidity("ZSEA", df)
    assert v.status == "locked"
    assert v.run_length >= HARD_LOCK_BARS


def test_soft_threshold_flags_before_hard():
    """A 5-9 bar lock is 'suspicious' (early warning) but not 'locked'."""
    prices = [10.0] * SOFT_LOCK_BARS
    locks = [True] * SOFT_LOCK_BARS
    df = _df(prices, locks=locks)
    v = detect_illiquidity("SOFT", df)
    assert v.status == "suspicious"


def test_scan_universe_only_bamb_flags():
    """Across a deterministic universe, only frozen BAMB is flagged."""
    universe = ["ABSA", "COOP", "EABL", "SCOM", "KPLC", "KCB", "SCBK",
                "BAMB", "TOTL", "KNRE", "EQTY", "WTK"]
    bars = {s: _df([10.0] * 20) for s in universe}
    bars["BAMB"] = _df([10.0] * (HARD_LOCK_BARS + 5),
                        locks=[True] * (HARD_LOCK_BARS + 5))
    res = scan_universe(bars)
    flagged = [s for s, v in res.items() if v.status != "healthy"]
    assert flagged == ["BAMB"], f"expected only BAMB, got {flagged}"


def test_detector_is_fail_open_on_bad_data():
    """Garbage input returns healthy, never raises."""
    v = detect_illiquidity("BAD", None)
    assert v.status == "healthy"
    v2 = detect_illiquidity("BAD", pd.DataFrame())
    assert v2.status == "healthy"
