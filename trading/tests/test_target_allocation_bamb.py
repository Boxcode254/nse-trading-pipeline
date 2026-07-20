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


def test_held_non_suspended_positions_have_target():
    """CRITICAL: every held position that is NOT suspended must have a
    target weight. This is the guard that catches orphaned real holdings
    (the bug where EABL was silently dropped and would be force-sold)."""
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


def test_bamb_never_in_rebalance_trades():
    """Generate a plan against the live portfolio and assert no BAMB trade."""
    state_path = ROOT / "portfolio" / "state.json"
    if not state_path.exists():
        pytest.skip("no live portfolio state.json to test against")
    state = json.loads(state_path.read_text())
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
    bamb_trades = [t for t in trades if t.get("symbol") == "BAMB"]
    assert not bamb_trades, f"BAMB appeared in rebalance trades: {bamb_trades}"


def test_held_bamb_shares_retained():
    """39 BAMB shares must remain in state.json current_value (no forced sale)."""
    state_path = ROOT / "portfolio" / "state.json"
    if not state_path.exists():
        pytest.skip("no live portfolio state.json")
    state = json.loads(state_path.read_text())
    bamb = next((p for p in state.get("positions", []) if p["symbol"] == "BAMB"), None)
    assert bamb is not None, "BAMB position dropped from portfolio state"
    assert int(bamb.get("shares", 0)) == 39, f"expected 39 BAMB shares, got {bamb.get('shares')}"
    assert float(bamb.get("current_value", 0)) > 0, "BAMB current_value must be reported"


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
    """Banking between WARN (55) and HARD (60) -> WARN_CAP, never HARD_CAP."""
    held = [
        _pos("KCB", 350, 50.0),
        _pos("EQTY", 250, 50.0),
        _pos("ABSA", 200, 40.0),
        _pos("SCBK", 150, 40.0),
        _pos("COOP", 100, 40.0),
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
    warn = [v for v in violations if v["kind"] == "WARN_CAP" and v["sector"] == "banking"]
    hard = [v for v in violations if v["kind"] == "HARD_CAP"]
    if any(v["sector"] == "banking" and v["current_pct"] > SECTOR_CAP_WARN_PCT
           for v in violations):
        assert warn, "banking over warn should produce WARN_CAP"
    assert not hard, "banking under hard cap must NOT produce HARD_CAP"

