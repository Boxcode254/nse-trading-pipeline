"""Tests for trading.risk_profiles — fail-open vol/liq/corr helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np

from trading import risk_profiles as rp


def test_liquidity_score_in_range():
    assert 0.0 <= rp.liquidity_score("SCOM", signals={}) <= 1.0


def test_liquidity_score_uses_signal():
    # ranking liquidity factor is 0..100; 90 -> 0.9
    assert abs(rp.liquidity_score("SCOM", signals={"SCOM": {"liquidity": 90}}) - 0.9) < 1e-9
    # garbage falls back to neutral 0.5
    assert rp.liquidity_score("SCOM", signals={"SCOM": {"liquidity": "n/a"}}) == 0.5


def test_realized_vol_fallback_to_signal():
    assert rp.realized_vol("KCB", history=None, signal_vol=0.6) == 0.6


def test_realized_vol_from_returns():
    closes = np.array([100, 102, 101, 103, 105, 104, 106], dtype=float)
    v = rp.realized_vol("X", history=closes, signal_vol=0.5)
    assert 0.0 < v <= 1.0  # normalised 0..1


def test_realized_vol_empty_history_falls_back():
    assert rp.realized_vol("X", history=np.array([]), signal_vol=0.42) == 0.42


def test_pairwise_corr_fallback_positive():
    assert 0.0 <= rp.pairwise_corr("KCB", "COOP", matrix=None) <= 1.0


def test_pairwise_corr_uses_matrix():
    m = {"KCB": {"COOP": 0.9}}
    assert rp.pairwise_corr("KCB", "COOP", matrix=m) == 0.9


def test_sector_corr_penalty_banks_higher_than_cross_sector():
    pen_banks = rp.corr_penalty("KCB", ["EQTY", "ABSA", "SCBK", "COOP"], matrix=None)
    pen_xsec = rp.corr_penalty("KCB", ["SCOM", "EABL"], matrix=None)
    assert pen_banks > pen_xsec


def test_risk_weight_favours_low_vol_and_liquid():
    low = rp.risk_weight("A", vol=0.2, liq=0.95, corr_penalty_norm=0.1)
    high = rp.risk_weight("B", vol=0.8, liq=0.3, corr_penalty_norm=0.6)
    assert low > high
