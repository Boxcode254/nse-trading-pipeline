"""Phase 3 allocation-refinement tests (Tasks 2-4): risk-aware sizing,
decision.nse_only agreement, and verify_target_agreement gate.

These are deterministic: they pass synthetic portfolios / signals so they
don't depend on live NSE prices, MTM state, or network access.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from trading.target_allocation import (
    generate_rebalance_plan,
    get_target_allocations,
    verify_target_agreement,
    _risk_weights_for_sector,
)
from trading.services.decision import generate_proposal


# ── Task 2: risk-aware sizing ──────────────────────────────────────────────

def _pos(symbol, shares, price):
    return {"symbol": symbol, "shares": shares, "avg_cost": price,
            "current_value": shares * price}


def test_risk_weights_favour_low_vol_liquid():
    stocks = ["KCB", "EQTY", "COOP"]
    # COOP: low vol, high liq -> should dominate the split
    sigs = {"KCB": {"volatility": 80, "liquidity": 90},
            "EQTY": {"volatility": 80, "liquidity": 90},
            "COOP": {"volatility": 30, "liquidity": 95}}
    w = _risk_weights_for_sector("banking", stocks, signals=sigs)
    assert w["COOP"] > w["KCB"]
    assert abs(sum(w.values()) - 1.0) < 1e-6


def test_rebalance_plan_carries_risk_field():
    portfolio = {
        "cash": 200_000.0, "initial_capital": 300_000.0,
        "positions": [_pos("KCB", 100, 50.0)],  # banking under target -> top-up/add
    }
    prices = {"KCB": 50.0, "EQTY": 86.0, "ABSA": 33.0, "SCBK": 220.0,
              "COOP": 18.0, "SCOM": 35.0, "EABL": 275.0, "KPLC": 20.0,
              "TOTL": 40.0, "KNRE": 3.5, "BRIT": 7.0}
    signals = [{"symbol": s, "score": 70, "volatility": 50, "liquidity": 60}
               for s in prices]
    plan = generate_rebalance_plan(
        signals=signals, prices=prices, portfolio=portfolio, dry_run=True,
    )
    assert plan["summary"]["qty_mode"] == "delta"
    for t in plan["trades"]:
        assert "risk" in t, f"trade missing risk field: {t}"
        assert "vol" in t["risk"] and "liq" in t["risk"]
        # contract unchanged: delta_shares == shares
        assert t["delta_shares"] == t["shares"] > 0


def test_risk_aware_split_over_equal_split():
    """With mixed vols and an uncapped sector gap, a low-vol name gets MORE
    of the top-up than a high-vol name in the same sector (vs equal split).

    Hold SCBK (a banking name) so banking is under target but the remaining
    gap is small enough to dodge the 5% daily-shift cap, leaving KCB/COOP to
    split the top-up by risk weight.
    """
    portfolio = {
        "cash": 6_000_000.0,
        "initial_capital": 10_000_000.0,
        "positions": [_pos("SCBK", 40_000, 100.0)],  # 4.0M of 4.88M banking target
    }
    prices = {"SCBK": 100.0, "KCB": 100.0, "COOP": 100.0}
    # KCB high vol (90), COOP low vol (20)
    signals = [{"symbol": s, "score": 70, "volatility": v, "liquidity": 90}
               for s, v in (("KCB", 90), ("COOP", 20), ("SCBK", 50))]
    plan = generate_rebalance_plan(
        signals=signals, prices=prices, portfolio=portfolio, dry_run=True,
    )
    buys = {t["symbol"]: t["value"] for t in plan["trades"] if t["side"] == "BUY"}
    assert "COOP" in buys and "KCB" in buys, f"expected both new buys: {buys}"
    assert buys["COOP"] > buys["KCB"], (
        f"expected COOP (low vol) > KCB (high vol): {buys}")


# ── Task 3: decision.nse_only reuses target_allocation ──────────────────────

def test_decision_nse_only_sources_target_allocation():
    ta = get_target_allocations()
    prop = generate_proposal(tilt="Balanced", nse_only=True)
    eq_lines = {l.symbol: l.target_pct for l in prop.allocations
                if l.category == "equities"}
    for sym, pct in ta.items():
        assert abs(eq_lines.get(sym, 0.0) - pct) < 0.01, (
            f"{sym}: decision={eq_lines.get(sym)} target_allocation={pct}")
    # deferred buckets must be zero in nse_only mode
    assert prop.summary.get("forex", 0) == 0
    assert prop.summary.get("commodity", 0) == 0
    assert prop.summary.get("fixed_income", 0) == 0


# ── Task 4: verify_target_agreement gate ────────────────────────────────────

def test_verify_agreement_nse_only_true():
    rep = verify_target_agreement(nse_only=True)
    assert rep["agreed"] is True
    assert rep["max_abs_diff"] <= rep["tolerance"]


def test_verify_agreement_multi_asset_wellformed():
    rep = verify_target_agreement(nse_only=False)
    assert "per_stock" in rep and "agreed" in rep and "max_abs_diff" in rep
    # multi-asset decision splits into cash/forex/gold/tbills too, so equities
    # bucket < 90% -> normalised diff can exceed tolerance; just assert shape.
    assert isinstance(rep["per_stock"], dict)
