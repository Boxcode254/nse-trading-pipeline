"""Regression test: BAMB must never be a rebalance candidate.

BAMB (Bamburi Cement) is suspended from the NSE (28-Feb-2025, Amsons
buyout + CMA squeeze-out). It must be:
  * absent from every STRATEGY sector's stock list
  * absent from _strategy_universe()
  * excluded from any generated rebalance plan's buy/sell trades
  * retained as a static, non-rebalanceable position in portfolio state
    (39 held shares must survive — no forced SELL, no drop from totals)

Run: pytest trading/tests/test_target_allocation_bamb.py

STANDING CHECKLIST — every change to trading/target_allocation.py /
strategy config MUST keep these tests green AND add a test for the
APPROVED intent (not just internal consistency). Recurring gap week of
2026-07-20: "green" proved the code matched what was WRITTEN, not what
was APPROVED.
  * Every held non-suspended position has a target weight (orphan guard —
    this caught EABL being silently dropped on 2026-07-20, which would
    have force-sold 18 shares via the orphan-exit path).
  * Sector targets sum to 90 (invested) / 100 (incl 10 cash reserve).
  * SUSPENDED symbols never appear in buy/sell candidate lists.
  * If a hard sector cap exists, targets + tolerance must respect it.
"""
import json
import sys
from pathlib import Path

import pytest
from trading import config

# Make the trading package importable when run standalone
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from trading.target_allocation import (  # noqa: E402
    get_strategy,
    _strategy_universe,
    SUSPENDED,
    generate_rebalance_plan,
    validate_plan_constraints,
    SECTOR_CAP_HARD_PCT,
    SECTOR_CAP_WARN_PCT,
)

TARGET_INVESTED = 90.0  # 100 - CASH_RESERVE_PCT(10)


def test_weights_sum_to_invested():
    strat = get_strategy()
    total = sum(s["target_pct"] for s in strat.values())
    assert abs(total - TARGET_INVESTED) < 1.0, f"weights sum {total}, expected {TARGET_INVESTED}"


def test_manufacturing_bucket_removed():
    strat = get_strategy()
    assert "manufacturing" not in strat, "manufacturing bucket must be removed"


def test_bamb_not_in_any_sector_stocks():
    strat = get_strategy()
    for sec, cfg in strat.items():
        assert "BAMB" not in (cfg.get("stocks") or []), f"BAMB still in {sec}.stocks"
    assert "BAMB" in SUSPENDED, "BAMB must be in SUSPENDED sentinel"


@pytest.mark.live_data
def test_held_non_suspended_positions_have_target():
    """CRITICAL: every held position that is NOT suspended must have a
    target weight. This is the guard that catches orphaned real holdings
    (the bug where EABL was silently dropped and would be force-sold).

    TP-009: read-only invariant over the LIVE book (marked live_data —
    it reads production state but asserts no historical counts).
    """
    strat = get_strategy()
    uni = _strategy_universe()
    state_path = ROOT / "portfolio" / "state.json"
    if not state_path.exists():
        pytest.skip("no live portfolio state.json")
    state = json.loads(state_path.read_text())
    for p in state.get("positions", []):
        sym = p["symbol"]
        if sym in SUSPENDED:
            continue  # suspended holdings are intentionally excluded
        assert sym in uni, (
            f"HELD position {sym} has NO target weight and is not "
            f"SUSPENDED — would be orphan-sold on next rebalance"
        )


def test_eabl_has_target_weight():
    """EABL must retain manufacturing's remaining ~6.5% (not be orphaned)."""
    strat = get_strategy()
    eabl_sectors = [sec for sec, cfg in strat.items()
                    if "EABL" in (cfg.get("stocks") or [])]
    assert eabl_sectors, "EABL has no target sector"
    assert abs(strat[eabl_sectors[0]]["target_pct"] - 6.50) < 0.1, \
        f"EABL target {strat[eabl_sectors[0]]['target_pct']}, expected ~6.50"


def _fixture_state_with_bamb():
    """Hand-built book containing the suspended BAMB legacy holding (39 sh).

    TP-009: the live book legitimately no longer holds BAMB (the position was
    closed historically), so the *invariant* — suspended names are never
    force-sold and stay reported with current_value > 0 IF present — must be
    tested against a fixture rather than mutable production state.
    """
    positions = [
        {"symbol": "BAMB", "shares": 39, "avg_cost": 54.0,
         "total_cost": 2106.0, "current_value": 2106.0},
        {"symbol": "KCB", "shares": 135, "avg_cost": 92.25,
         "total_cost": 12453.75, "current_value": 12453.75},
        {"symbol": "EABL", "shares": 18, "avg_cost": 286.0,
         "total_cost": 5148.0, "current_value": 5148.0},
        {"symbol": "SCOM", "shares": 402, "avg_cost": 35.85,
         "total_cost": 14411.7, "current_value": 14411.7},
    ]
    cash = 60000.0
    return {
        "cash": cash,
        "initial_capital": 100000.0,
        "max_drawdown_pct": 0.0,
        "positions": positions,
        "total_value": cash + sum(p["current_value"] for p in positions),
    }


def test_bamb_never_in_rebalance_trades():
    """A fixture book holding BAMB must never produce a BAMB trade."""
    state = _fixture_state_with_bamb()
    prices = {p["symbol"]: p["current_value"] / p["shares"] for p in state["positions"]}

    plan = generate_rebalance_plan(
        signals=[{"symbol": s, "score": 50} for s in prices],
        prices=prices,
        portfolio=state,
        dry_run=True,
    )
    trades = plan.get("trades", [])
    bamb_trades = [t for t in trades if t.get("symbol") == "BAMB"]
    assert not bamb_trades, f"BAMB appeared in rebalance trades: {bamb_trades}"


def test_suspended_bamb_retained_and_never_force_sold(tmp_path, monkeypatch):
    """A fixture state.json holding BAMB: 39 shares survive, value reported.

    The invariant that matters is "suspended names are never force-sold and
    remain reported with current_value > 0 IF present" — not that the live
    book currently holds BAMB (it may legitimately not).
    """
    import trading.target_allocation as ta

    state = _fixture_state_with_bamb()
    pdir = tmp_path / "portfolio"
    pdir.mkdir()
    state_file = pdir / "state.json"
    state_file.write_text(json.dumps(state))
    monkeypatch.setattr(ta, "STATE_PATH", state_file)
    monkeypatch.setattr(ta, "MTM_PATH", tmp_path / "no_mtm_state.json")

    loaded = ta._load_portfolio()
    bamb = next((p for p in loaded.get("positions", []) if p["symbol"] == "BAMB"), None)
    assert bamb is not None, "suspended holding must survive the state read"
    assert int(bamb.get("shares", 0)) == 39, f"expected 39 BAMB shares, got {bamb.get('shares')}"
    assert float(bamb.get("current_value", 0)) > 0, "BAMB current_value must be reported"

    weights = ta.compute_sector_weights(loaded)
    reported = {s for sec in weights["sectors"].values() for s in sec["stocks"]}
    assert "BAMB" in reported, "suspended holding was dropped from reported sector weights"
    assert weights["total_value"] == pytest.approx(state["total_value"])

    prices = {p["symbol"]: p["current_value"] / p["shares"] for p in loaded["positions"]}
    plan = ta.generate_rebalance_plan(
        signals=[{"symbol": s, "score": 50} for s in prices],
        prices=prices,
        portfolio=loaded,
        dry_run=True,
    )
    bamb_trades = [t for t in plan.get("trades", []) if t.get("symbol") == "BAMB"]
    assert not bamb_trades, f"suspended BAMB must never be force-sold, got: {bamb_trades}"


@pytest.mark.live_data
def test_live_book_never_force_sells_suspended_holdings():
    """Read-only invariant smoke over the LIVE book (no historical counts).

    Whatever the live portfolio holds today, any SUSPENDED symbol in it must
    receive no BUY/SELL from a generated plan. Marked ``live_data`` so it can
    be deselected with ``-m "not live_data"``.
    """
    state_path = ROOT / "portfolio" / "state.json"
    if not state_path.exists():
        pytest.skip("no live portfolio state.json to test against")
    state = json.loads(state_path.read_text())
    held_suspended = [p["symbol"] for p in state.get("positions", [])
                      if p["symbol"] in SUSPENDED]

    prices = {p["symbol"]: p.get("current_value", 0) / max(p.get("shares", 1), 1)
              for p in state.get("positions", [])}
    # Floor any zero price so the engine doesn't bail on missing data
    for sym in prices:
        prices[sym] = max(prices[sym], 1.0)

    plan = generate_rebalance_plan(
        signals=[{"symbol": s, "score": 50} for s in prices],
        prices=prices,
        portfolio=state,
        dry_run=True,
    )
    trades = plan.get("trades", [])
    for sym in held_suspended:
        bad = [t for t in trades if t.get("symbol") == sym]
        assert not bad, f"suspended holding {sym} received a trade: {bad}"
    assert not [t for t in trades if t.get("symbol") in SUSPENDED]


# ─────────────────────────────────────────────────────────────────────────────
# Concentration guardrails: HIGH cap (60 hard / 55 warn) + LOW floor
# ─────────────────────────────────────────────────────────────────────────────

def _fake_portfolio(total_value=100000.0, positions=None):
    """Build a minimal portfolio dict for deterministic plan tests."""
    positions = positions or []
    invested = sum(p["current_value"] for p in positions)
    return {
        "total_value": total_value,
        "cash": total_value - invested,
        "initial_capital": total_value,
        "positions": positions,
    }


def _pos(symbol, shares, price):
    return {
        "symbol": symbol,
        "shares": shares,
        "avg_cost": price,
        "current_value": shares * price,
    }


def test_eabl_orphan_fires_floor_violation():
    """Today's ACTUAL bug: EABL silently dropped from strategy.

    With the current (fixed) strategy EABL IS in 'consumer'. To reproduce
    the orphan scenario we must temporarily remove EABL from STRATEGY.
    We do that by monkeypatching get_strategy() to return a strategy
    without EABL, then assert validate_plan_constraints flags a FLOOR
    error for the held EABL position and no SELL is emitted for it.
    """
    import trading.target_allocation as ta

    held = [_pos("EABL", 18, 275.0), _pos("KCB", 100, 50.0)]
    portfolio = _fake_portfolio(positions=held)

    # Strategy with EABL MISSING (reproduces the 2026-07-20 orphan state)
    orphan_strategy = {k: v for k, v in get_strategy().items() if "EABL" not in v.get("stocks", [])}
    assert any("EABL" in v.get("stocks", []) for v in get_strategy().values()), \
        "precondition: EABL should be present in the real strategy"
    assert not any("EABL" in v.get("stocks", []) for v in orphan_strategy.values()), \
        "precondition: orphan strategy must exclude EABL"

    real_get = ta.get_strategy
    ta.get_strategy = lambda: dict(orphan_strategy)
    try:
        weights = ta.compute_sector_weights(portfolio)
        targets = ta.compute_targets(weights)
        prices = {"EABL": 275.0, "KCB": 50.0}
        plan = generate_rebalance_plan(
            signals=[{"symbol": s, "score": 50} for s in prices],
            prices=prices, portfolio=portfolio, dry_run=True,
        )
        violations = plan.get("violations", [])
    finally:
        ta.get_strategy = real_get

    floor_v = [v for v in violations if v["kind"] == "FLOOR" and v.get("symbol") == "EABL"]
    assert floor_v, f"Expected FLOOR violation for orphaned EABL, got: {violations}"
    assert floor_v[0]["level"] == "error", "orphan FLOOR must be error-level"

    eabl_sells = [t for t in plan["trades"] if t["symbol"] == "EABL" and t["side"] == "SELL"]
    assert not eabl_sells, f"Orphan guard must suppress EABL SELL, but got: {eabl_sells}"


def test_banking_blowout_fires_hard_cap():
    """Synthetic banking blowout: banking at 62% must trip HARD_CAP."""
    held = [
        _pos("KCB", 400, 50.0),
        _pos("EQTY", 300, 50.0),
        _pos("ABSA", 300, 40.0),
        _pos("SCBK", 200, 40.0),
        _pos("COOP", 150, 40.0),
        _pos("SCOM", 100, 25.0),
        _pos("EABL", 18, 275.0),
    ]
    portfolio = _fake_portfolio(total_value=100000.0, positions=held)
    prices = {p["symbol"]: p["avg_cost"] for p in held}

    plan = generate_rebalance_plan(
        signals=[{"symbol": s, "score": 50} for s in prices],
        prices=prices, portfolio=portfolio, dry_run=True,
    )
    violations = plan.get("violations", [])
    hard = [v for v in violations if v["kind"] == "HARD_CAP" and v["sector"] == "banking"]
    assert hard, f"Expected HARD_CAP for banking blowout, got: {violations}"
    assert hard[0]["current_pct"] > SECTOR_CAP_HARD_PCT
    bank_sells = [t for t in plan["trades"]
                  if t["side"] == "SELL" and t["sector"] == "banking"]
    assert bank_sells, f"HARD_CAP must produce a banking trim, got trades: {plan['trades']}"


def test_banking_warn_cap_no_hard():
    """Banking between tiered WARN and HARD caps -> WARN_CAP only."""
    held = [
        _pos("KCB", 420, 100.0),
        _pos("SCOM", 100, 50.0),
        _pos("EABL", 18, 275.0),
    ]
    portfolio = _fake_portfolio(total_value=100000.0, positions=held)
    prices = {p["symbol"]: p["avg_cost"] for p in held}

    plan = generate_rebalance_plan(
        signals=[{"symbol": s, "score": 50} for s in prices],
        prices=prices, portfolio=portfolio, dry_run=True,
    )
    violations = plan.get("violations", [])
    warn = [v for v in violations if v["kind"] == "WARN_CAP" and v["sector"] == "banking"]
    hard = [v for v in violations if v["kind"] == "HARD_CAP"]
    assert warn, f"banking above tiered warn should produce WARN_CAP: {violations}"
    assert not hard, "banking under hard cap must NOT produce HARD_CAP"


def test_tiered_banking_caps_are_canonical(tmp_path):
    """Base tiered caps are canonical when the momentum gate has no evidence.

    Deterministic by construction: the momentum uplift (TP-002) now reads the
    live ``data/`` cache, so asserting live caps here would depend on today's
    prices. The uplift itself is covered in
    ``test_sector_momentum_cap.py``.
    """
    assert config.sector_cap("banking", data_dir=tmp_path) == {"warn": 40.0, "hard": 45.0}
    assert config.sector_cap("banking")["warn"] == 40.0
    assert config.sector_cap("banking")["hard"] >= 45.0

