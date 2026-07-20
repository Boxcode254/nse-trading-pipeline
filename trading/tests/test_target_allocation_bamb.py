"""Regression test: BAMB must never be a rebalance candidate.

BAMB (Bamburi Cement) is suspended from the NSE (28-Feb-2025, Amsons
buyout + CMA squeeze-out). It must be:
  * absent from every STRATEGY sector's stock list
  * absent from _strategy_universe()
  * excluded from any generated rebalance plan's buy/sell trades
  * retained as a static, non-rebalanceable position in portfolio state
    (39 held shares must survive — no forced SELL, no drop from totals)

Run: pytest trading/tests/test_target_allocation_bamb.py
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


def test_bamb_not_in_strategy_universe():
    uni = _strategy_universe()
    assert "BAMB" not in uni, "BAMB must not be a rebalance candidate universe symbol"


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
