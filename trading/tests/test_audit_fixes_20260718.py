"""Integration tests for audit fixes (2026-07-18).

Covers:
1. Shares contract — plan delta must never be treated as absolute target
2. Jul-17 freeze regression — holding > plan delta must still allow buys
3. Canonical sector map alignment
4. Orphan (WTK) force-exit in rebalance plan
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trading import config
from trading.auto_trader import (
    _available_cash_for_buys,
    _plan_delta,
    _sector_of,
    run_auto_trade,
)
from trading.target_allocation import (
    SECTOR_MAP as TA_SECTOR_MAP,
    STRATEGY,
    _delta_trade,
    _strategy_universe,
    generate_rebalance_plan,
)


# ── Shares contract ────────────────────────────────────────────────────────


def test_delta_trade_contract_fields():
    t = _delta_trade(
        symbol="EQTY", side="BUY", delta_shares=58, price=86.0,
        reason="top-up", sector="banking", signal_score=60,
    )
    assert t["qty_mode"] == "delta"
    assert t["delta_shares"] == 58
    assert t["shares"] == 58  # alias MUST equal delta
    assert t["value"] == round(58 * 86.0, 2)


def test_plan_delta_prefers_delta_shares():
    assert _plan_delta({"delta_shares": 10, "shares": 999}) == 10
    assert _plan_delta({"shares": 7}) == 7
    assert _plan_delta({}) == 0


def test_available_cash_when_above_ten_percent_reserve():
    available = _available_cash_for_buys(cash=15_000.0, total_before=100_000.0)
    assert available == pytest.approx(5_000.0)


def test_available_cash_floor_prevents_reserve_deadlock(monkeypatch):
    monkeypatch.setattr(config, "CASH_RESERVE_PCT", 20.0)
    available = _available_cash_for_buys(cash=16_320.34, total_before=103_241.25)
    assert available == pytest.approx(816.017, abs=0.01)


def test_jul17_freeze_regression_does_not_skip_as_target_met(tmp_path, monkeypatch):
    """Replay Jul-17 condition: hold 67 EQTY, plan says buy 58 more.

    Old bug: treated 58 as absolute target → 'Target met — hold 67 (target 58)'.
    New contract: buy up to 58 shares (cash permitting).
    """
    portfolio_dir = tmp_path / "portfolio"
    portfolio_dir.mkdir()
    (tmp_path / "cache").mkdir()
    state = {
        "cash": 50_000.0,
        "initial_capital": 100_000.0,
        "positions": [
            {
                "symbol": "EQTY",
                "shares": 67,
                "avg_cost": 86.75,
                "total_cost": 5812.25,
                "current_value": 5812.25,
            }
        ],
        "created_at": "2026-07-13T13:52:30+03:00",
        "updated_at": "2026-07-16T10:31:37+03:00",
        "max_drawdown_pct": 0.0,
    }
    (portfolio_dir / "state.json").write_text(json.dumps(state))
    (portfolio_dir / "transactions.json").write_text("[]")
    (portfolio_dir / "snapshots.json").write_text("[]")

    fake_plan = {
        "trades": [
            {
                "symbol": "EQTY",
                "side": "BUY",
                "delta_shares": 58,
                "shares": 58,
                "qty_mode": "delta",
                "price": 86.0,
                "value": 58 * 86.0,
                "reason": "Top-up EQTY: sector banking is -28.0% under target",
                "sector": "banking",
                "signal_score": 60,
            }
        ],
        "summary": {"trade_count": 1, "qty_mode": "delta"},
        "targets": {},
    }

    def _fake_expanduser(path):
        s = str(path)
        if s.rstrip("/").endswith(".trading/portfolio"):
            return str(portfolio_dir)
        if s.rstrip("/").endswith(".trading/cache"):
            return str(tmp_path / "cache")
        if s.rstrip("/").endswith(".trading"):
            return str(tmp_path)
        return s

    monkeypatch.delenv("MANSA_API_KEY", raising=False)
    monkeypatch.setattr("trading.auto_trader.os.path.expanduser", _fake_expanduser)
    monkeypatch.setattr(
        "trading.target_allocation.generate_rebalance_plan",
        lambda **kwargs: fake_plan,
    )
    monkeypatch.setattr(
        "trading.auto_trader._price_map",
        lambda symbols: {s: 86.0 for s in symbols},
    )
    monkeypatch.setattr(
        "trading.portfolio.engine._default_portfolio_dir",
        lambda: str(portfolio_dir),
    )

    report = run_auto_trade(dry_run=True)

    skip_reasons = " | ".join(s["reason"] for s in report.stocks_skipped)
    assert "Target met" not in skip_reasons, f"Jul-17 freeze regressed: {skip_reasons}"
    assert any(b["symbol"] == "EQTY" for b in report.stocks_bought), (
        f"Expected EQTY buy; bought={report.stocks_bought} skipped={report.stocks_skipped}"
    )
    eqty_buy = next(b for b in report.stocks_bought if b["symbol"] == "EQTY")
    assert eqty_buy["shares"] == 58


def test_generate_plan_emits_qty_mode_delta():
    portfolio = {
        "cash": 50_000.0,
        "initial_capital": 100_000.0,
        "positions": [
            {"symbol": "KCB", "shares": 36, "avg_cost": 80.0,
             "total_cost": 2880.0, "current_value": 2880.0},
            {"symbol": "WTK", "shares": 11, "avg_cost": 159.0,
             "total_cost": 1749.0, "current_value": 1749.0},
        ],
    }
    prices = {"KCB": 80.0, "WTK": 159.0, "EQTY": 86.0, "ABSA": 33.0,
              "SCOM": 35.0, "BRIT": 7.0, "KNRE": 3.5}
    # High scores so signal gate passes
    signals = [{"symbol": s, "score": 70} for s in prices]

    plan = generate_rebalance_plan(
        signals=signals, prices=prices, portfolio=portfolio, dry_run=True,
    )
    assert plan["summary"].get("qty_mode") == "delta"
    for t in plan["trades"]:
        assert t.get("qty_mode") == "delta"
        assert t["delta_shares"] == t["shares"]
        assert t["delta_shares"] > 0


def test_orphan_wtk_gets_full_exit_in_plan():
    portfolio = {
        "cash": 50_000.0,
        "initial_capital": 100_000.0,
        "positions": [
            {"symbol": "WTK", "shares": 11, "avg_cost": 159.0,
             "total_cost": 1749.0, "current_value": 1749.0},
            {"symbol": "SCOM", "shares": 100, "avg_cost": 35.0,
             "total_cost": 3500.0, "current_value": 3500.0},
        ],
    }
    prices = {"WTK": 159.0, "SCOM": 35.0}
    signals = [{"symbol": "WTK", "score": 40}, {"symbol": "SCOM", "score": 55}]
    plan = generate_rebalance_plan(
        signals=signals, prices=prices, portfolio=portfolio, dry_run=True,
    )
    wtk_sells = [
        t for t in plan["trades"]
        if t["symbol"] == "WTK" and t["side"] == "SELL"
    ]
    assert wtk_sells, f"Expected WTK orphan sell, got {plan['trades']}"
    assert wtk_sells[0]["delta_shares"] == 11
    assert "Orphan" in wtk_sells[0]["reason"]


# ── Sector map ─────────────────────────────────────────────────────────────


def test_canonical_sector_map_aligned():
    # After BAMB excision (2026-07-20) EABL kept manufacturing's remaining
    # share as its own "consumer" sector. All sector vocabularies must agree.
    assert config.SECTOR_MAP["EABL"] == "consumer"
    assert config.SECTOR_MAP["KPLC"] == "energy"
    assert config.get_sector("EABL") == "consumer"
    assert config.get_sector("KPLC") == "energy"
    assert _sector_of("EABL") == "consumer"
    assert _sector_of("KPLC") == "energy"
    # target_allocation re-exports same map
    assert TA_SECTOR_MAP["EABL"] == config.SECTOR_MAP["EABL"]
    assert TA_SECTOR_MAP["KPLC"] == config.SECTOR_MAP["KPLC"]
    # ASSET_CATEGORIES aligned
    assert config.ASSET_CATEGORIES["EABL"]["sector"] == "consumer"
    assert config.ASSET_CATEGORIES["KPLC"]["sector"] == "energy"


def test_strategy_universe_excludes_wtk():
    uni = _strategy_universe()
    assert "KCB" in uni
    assert "SCOM" in uni
    assert "WTK" not in uni
    # all strategy stocks covered
    for sec, cfg in STRATEGY.items():
        for s in cfg["stocks"]:
            assert s in uni
