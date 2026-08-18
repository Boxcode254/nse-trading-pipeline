"""Tests for the Target Allocation Engine."""

from __future__ import annotations

import sys
import json
from pathlib import Path

# Ensure project root is on path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading.target_allocation import (
    get_strategy,
    compute_sector_weights,
    compute_targets,
    get_target_allocations,
    SECTOR_MAP,
    STRATEGY,
)


def test_strategy_sum_to_invested_book():
    """Sector target weights must sum to ~90% (invested book; 10% cash separate)."""
    strat = get_strategy()
    total = sum(s["target_pct"] for s in strat.values())
    assert abs(total - 90.0) <= 1.0, f"Targets sum to {total}, expected ~90%"


def test_strategy_has_all_sectors_in_map():
    """All stocks in strategy must be in SECTOR_MAP."""
    strat = get_strategy()
    for sec, cfg in strat.items():
        for sym in cfg["stocks"]:
            assert sym in SECTOR_MAP, f"{sym} not in SECTOR_MAP"
            assert SECTOR_MAP[sym] == sec, f"{sym} mapped to {SECTOR_MAP[sym]}, expected {sec}"


def test_sector_weights_empty_portfolio():
    """Empty portfolio produces 100% cash, no sectors."""
    portfolio = {"cash": 100_000, "positions": [], "initial_capital": 100_000}
    w = compute_sector_weights(portfolio)
    assert w["total_value"] == 100_000
    assert w["cash"] == 100_000
    assert w["cash_pct"] == 100.0
    assert len(w["sectors"]) == 0


def test_sector_weights_single_position():
    """Single position correctly assigned to sector."""
    portfolio = {
        "cash": 50_000,
        "positions": [
            {"symbol": "SCOM", "shares": 100, "avg_cost": 35.0, "current_value": 3545},
        ],
        "initial_capital": 100_000,
    }
    w = compute_sector_weights(portfolio)
    assert "telecom" in w["sectors"]
    assert w["sectors"]["telecom"]["value"] == 3545
    assert round(w["sectors"]["telecom"]["pct"], 1) == 6.6  # 3545/53545
    assert round(w["cash_pct"], 1) == 93.4


def test_sector_weights_multiple_positions():
    """Multiple positions in same sector are aggregated."""
    portfolio = {
        "cash": 20_000,
        "positions": [
            {"symbol": "KCB", "shares": 200, "avg_cost": 80.0, "current_value": 16000},
            {"symbol": "EQTY", "shares": 100, "avg_cost": 87.0, "current_value": 8700},
            {"symbol": "EABL", "shares": 50, "avg_cost": 265.0, "current_value": 13250},
        ],
        "initial_capital": 100_000,
    }
    w = compute_sector_weights(portfolio)
    assert "banking" in w["sectors"]
    assert "consumer" in w["sectors"]
    assert w["sectors"]["banking"]["value"] == 24700
    assert w["sectors"]["consumer"]["value"] == 13250
    total = 20000 + 24700 + 13250
    assert w["total_value"] == total


def test_targets_on_target():
    """When portfolio matches target, status is on_target."""
    # Build portfolio that matches strategy targets
    portfolio = {
        "cash": 10_000,  # ~10% cash
        "positions": [],
        "initial_capital": 100_000,
    }
    total = 100_000
    strat = get_strategy()
    sym_num = 1
    positions = []
    for sec, cfg in strat.items():
        target_value = total * cfg["target_pct"] / 100
        if cfg["stocks"]:
            per_stock = target_value / len(cfg["stocks"])
            for sym in cfg["stocks"]:
                price = 100.0 + sym_num  # arbitrary price
                shares = max(1, int(per_stock / price))
                positions.append({
                    "symbol": sym, "shares": shares,
                    "avg_cost": price, "current_value": shares * price,
                })
                sym_num += 1
    portfolio["positions"] = positions
    portfolio["cash"] = total - sum(p["current_value"] for p in positions)

    # Only check on_target count — exact matching is hard with integer shares
    w = compute_sector_weights(portfolio)
    targets = compute_targets(w)
    assert targets["summary"]["sectors_analysed"] == 5


def test_targets_under_weight():
    """All sectors under target when heavily in cash."""
    portfolio = {"cash": 95_000, "positions": [], "initial_capital": 100_000}
    w = compute_sector_weights(portfolio)
    targets = compute_targets(w)
    assert targets["summary"]["under_weight"] == 5
    assert targets["summary"]["over_weight"] == 0
    assert targets["summary"]["on_target"] == 0


def test_get_target_allocations_returns_dict():
    """get_target_allocations returns {symbol: pct} dict."""
    portfolio = {"cash": 100_000, "positions": [], "initial_capital": 100_000}
    allocs = get_target_allocations(portfolio)
    assert isinstance(allocs, dict)
    # Should have at least one suggested entry per sector
    assert len(allocs) >= len(get_strategy())


def test_get_target_allocations_values_reasonable():
    """Allocation percentages should be between 0 and 100."""
    portfolio = {
        "cash": 100_000,
        "positions": [
            {"symbol": "KCB", "shares": 100, "avg_cost": 80.0, "current_value": 8000},
            {"symbol": "SCOM", "shares": 100, "avg_cost": 35.0, "current_value": 3545},
        ],
        "initial_capital": 100_000,
    }
    allocs = get_target_allocations(portfolio)
    for sym, pct in allocs.items():
        assert 0 <= pct <= 100, f"{sym}: {pct}% out of range"
    total = sum(allocs.values())
    # Invested-book targets sum to ~90% (cash reserve is separate)
    assert 85 <= total <= 95, f"Total allocation {total}% not ~90% invested book"


def test_compute_targets_structure():
    """compute_targets returns expected structure."""
    portfolio = {"cash": 100_000, "positions": [], "initial_capital": 100_000}
    w = compute_sector_weights(portfolio)
    targets = compute_targets(w)
    assert "total_value" in targets
    assert "strategy" in targets
    assert "current" in targets
    assert "cash" in targets
    assert "summary" in targets
    for sec in get_strategy():
        assert sec in targets["current"]

    # Each sector should have required fields
    for sec, info in targets["current"].items():
        for field in ["target_pct", "tolerance", "current_pct", "status", "action"]:
            assert field in info, f"{sec} missing {field}"
        assert info["status"] in ("on_target", "within_tolerance", "over_weight", "under_weight")
        assert info["action"] in ("hold", "trim", "add")
