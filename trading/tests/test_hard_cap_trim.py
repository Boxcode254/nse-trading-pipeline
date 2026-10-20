"""HARD cap trim sizing + honest diagnostics (t_326ee74d).

Covers three defects found in the 2026-10-20 production run:
  D1  the cap trim was sized as a single-step delta (KES 316 for SCBK where
      the post-trade ratio requires ~KES 1,023 = ~4 SCBK shares), and the
      executor then dropped it as "Sell too small" — breach persisted.
  D2  HARD_CAP_NO_TRIM ("no trim trade generated") was emitted even though a
      trim existed and was suppressed by the executor's minimum.

Policy under test: cap enforcement wins over the min-trade cost heuristic at
plan level — a HARD cap breach must end with an executable trim (post-trade
weight <= cap, value >= MIN_TRADE_KES) or an explicit, honest exception.
"""

from __future__ import annotations

import pytest

from trading import config as trading_config
from trading import target_allocation as ta
from trading.auto_trader import _min_trade_skip
from trading.target_allocation import (
    generate_rebalance_plan,
    hard_cap_required_sale_value,
)

SCBK_PRICE = 317.50


@pytest.fixture
def frozen_caps(monkeypatch):
    """Deterministic tiered caps: banking warn 40 / hard 45 (config defaults)."""

    def _cap(sec, **kwargs):
        if sec == "banking":
            return {"warn": 40.0, "hard": 45.0}
        return {"warn": 25.0, "hard": 30.0}

    monkeypatch.setattr(trading_config, "sector_cap", _cap)


def _pos(sym: str, shares: int, price: float) -> dict:
    return {
        "symbol": sym,
        "shares": shares,
        "avg_cost": price,
        "current_value": shares * price,
    }


def _breach_book() -> dict:
    """Mirror of the 2026-10-20 production book: banking 45,575 / 100,028."""
    positions = [
        _pos("COOP", 40, 135.0),    # banking   5,400
        _pos("KCB", 100, 143.0),    # banking  14,300
        _pos("SCBK", 50, SCBK_PRICE),  # banking 15,875 (largest -> trim target)
        _pos("ABSA", 100, 100.0),   # banking  10,000  -> sector 45,575
        _pos("SCOM", 100, 140.0),   # telecom  14,000  (13.99%, within tol)
        _pos("KPLC", 1000, 10.0),   # energy   10,000  (9.99%, within tol)
    ]
    cash = 100028.0 - sum(p["current_value"] for p in positions)
    return {"cash": cash, "positions": positions, "initial_capital": 100000}


def _plan(book: dict) -> dict:
    prices = {p["symbol"]: p["avg_cost"] for p in book["positions"]}
    signals = [{"symbol": s, "score": 50} for s in prices]
    return generate_rebalance_plan(
        signals=signals, prices=prices, portfolio=book, dry_run=True
    )


def _violation_kinds(plan: dict) -> set:
    return {v.get("kind") for v in plan.get("violations", [])}


# ── Unit: the fixed point, not a single-step delta ──────────────────────────


def test_fixed_point_solves_post_trade_ratio():
    # Production numbers: banking 45,575 of 100,028, hard cap 45%.
    # Single-step delta would say 45575 - 0.45*100028 = 562.4 (the old bug).
    # The true required sale solves (45575-x)/(100028-x) <= 0.45 -> ~1,023.
    required = hard_cap_required_sale_value(45575.0, 100028.0, 45.0)
    assert required == pytest.approx(1022.55, abs=0.5)
    assert required > 562.0  # strictly more than the single-step delta
    # Verify the inequality it claims:
    assert (45575.0 - required) / (100028.0 - required) <= 0.45
    # A single-step sale does NOT satisfy it:
    assert (45575.0 - 562.4) / (100028.0 - 562.4) > 0.45


def test_fixed_point_accounts_for_other_sector_sells():
    # Another sector selling 5,000 shrinks the denominator -> banking must
    # sell more than the no-other-sells answer.
    base = hard_cap_required_sale_value(45575.0, 100028.0, 45.0)
    with_other = hard_cap_required_sale_value(
        45575.0, 100028.0, 45.0, other_sells=5000.0
    )
    assert with_other > base
    assert (45575.0 - with_other) / (100028.0 - 5000.0 - with_other) <= 0.45


def test_fixed_point_no_breach_returns_zero():
    assert hard_cap_required_sale_value(40000.0, 100028.0, 45.0) == 0.0


# ── D1: breach -> executable, correctly-sized trim in the plan ──────────────


def test_breach_emits_executable_trim(frozen_caps):
    book = _breach_book()
    plan = _plan(book)
    banking_sells = [
        t for t in plan["trades"] if t["side"] == "SELL" and t["sector"] == "banking"
    ]
    assert banking_sells, "HARD cap breach must produce a banking trim in the plan"
    trim = banking_sells[0]
    # Largest holding (SCBK) gets the trim, rounded UP to whole shares
    assert trim["symbol"] == "SCBK"
    assert trim["delta_shares"] == 4  # ceil(1022.55 / 317.50) = 4
    # Executable: survives the executor's MIN_TRADE_KES floor
    assert trim["value"] >= trading_config.MIN_TRADE_KES
    # Independent recomputation of the post-trade weight (ALL sells applied):
    sold_bank = sum(t["value"] for t in banking_sells)
    sold_other = sum(
        t["value"] for t in plan["trades"]
        if t["side"] == "SELL" and t["sector"] != "banking"
    )
    post_w = (45575.0 - sold_bank) / (100028.0 - sold_bank - sold_other) * 100
    assert post_w <= 45.0, f"post-trade banking weight {post_w:.2f}% still over cap"
    kinds = _violation_kinds(plan)
    assert "HARD_CAP" in kinds
    assert "HARD_CAP_NO_TRIM" not in kinds
    assert "HARD_CAP_SUBMIN" not in kinds


def test_trim_survives_auto_trader_min_trade_filter(frozen_caps):
    """Drive the real executor filter over the plan's trim (t_e056a504 gate 2)."""
    book = _breach_book()
    plan = _plan(book)
    trim = next(
        t for t in plan["trades"]
        if t["side"] == "SELL" and t["sector"] == "banking"
    )
    # This mirrors exactly what run_auto_trade step 2 -> step 7 does with a
    # plan SELL trade (sector/hard_cap_trim metadata passed through):
    sell = {
        "symbol": trim["symbol"],
        "delta_shares": trim["delta_shares"],
        "reason": trim["reason"],
        "stop_loss": False,
        "sector_cap": False,
        "hard_cap_trim": trim.get("hard_cap_trim", False),
        "sector": trim.get("sector", ""),
        "sector_pct": trim.get("sector_pct"),
        "cap_hard_pct": trim.get("cap_hard_pct"),
    }
    sell_value = sell["delta_shares"] * SCBK_PRICE
    assert sell_value >= trading_config.MIN_TRADE_KES
    # The real filter: None = trade survives to the execution list
    assert _min_trade_skip(sell, sell_value) is None


# ── Tiny breach: explicit sub-minimum exception, never a lying diagnostic ───


def _tiny_breach_book() -> dict:
    """banking exactly 45,025 / 100,000 = 45.025% — required trim ~KES 45,
    so even the minimal 1-share SCBK sale (KES 317.50) is below MIN_TRADE_KES."""
    positions = [
        _pos("COOP", 110, 135.0),      # banking  14,850
        _pos("KCB", 100, 143.0),       # banking  14,300
        _pos("SCBK", 50, SCBK_PRICE),  # banking  15,875 -> sector 45,025
        _pos("SCOM", 100, 140.0),      # telecom  14,000
        _pos("KPLC", 1000, 10.0),      # energy   10,000
    ]
    invested = sum(p["current_value"] for p in positions)
    cash = 100000.0 - invested
    return {"cash": cash, "positions": positions, "initial_capital": 100000}


def test_tiny_breach_submin_exception_not_no_trim(frozen_caps):
    book = _tiny_breach_book()
    banking_val = sum(
        p["current_value"] for p in book["positions"]
        if p["symbol"] in ("COOP", "KCB", "SCBK")
    )
    assert banking_val == 45025.0
    assert banking_val / 100000.0 * 100 == pytest.approx(45.025)
    plan = _plan(book)
    banking_sells = [
        t for t in plan["trades"] if t["side"] == "SELL" and t["sector"] == "banking"
    ]
    # The minimal trim IS emitted (1 share), not silently dropped
    assert len(banking_sells) == 1
    trim = banking_sells[0]
    assert trim["delta_shares"] == 1
    assert trim["value"] == pytest.approx(SCBK_PRICE)
    # ...and the post-trade weight really is back under the cap
    post_w = (banking_val - SCBK_PRICE) / (100000.0 - SCBK_PRICE) * 100
    assert post_w <= 45.0
    kinds = _violation_kinds(plan)
    assert "HARD_CAP" in kinds
    # The honest diagnostics: sub-minimum exception YES, "no trim" NO
    assert "HARD_CAP_SUBMIN" in kinds
    assert "HARD_CAP_NO_TRIM" not in kinds
    submin = next(v for v in plan["violations"] if v["kind"] == "HARD_CAP_SUBMIN")
    assert "MIN_TRADE_KES" in submin["detail"]
    # Executor: the same trim is skipped with an honest message, not a bare
    # "Sell too small"
    msg = _min_trade_skip(
        {
            "sector_cap": False,
            "hard_cap_trim": True,
            "sector": "banking",
            "sector_pct": 45.03,
            "cap_hard_pct": 45.0,
        },
        SCBK_PRICE,
    )
    assert msg is not None
    assert "cannot be corrected by a trade this session" in msg
    assert "Sell too small" not in msg


# ── Regression: calm book -> no trim, no cap violations ─────────────────────


def test_calm_book_no_trim_no_cap_violations(frozen_caps):
    positions = [
        _pos("COOP", 100, 135.0),   # banking  13,500
        _pos("KCB", 100, 143.0),    # banking  14,300
        _pos("SCBK", 30, SCBK_PRICE),  # banking 9,525
        _pos("ABSA", 22, 100.0),    # banking   2,200 -> 39,525 (39.5%, < warn 40)
        _pos("SCOM", 107, 140.0),   # telecom 14,980 (14.98%)
        _pos("KPLC", 1200, 10.0),   # energy  12,000 (12.0%)
    ]
    invested = sum(p["current_value"] for p in positions)
    book = {
        "cash": 100000.0 - invested,
        "positions": positions,
        "initial_capital": 100000,
    }
    plan = _plan(book)
    assert not [
        t for t in plan["trades"] if t["side"] == "SELL"
    ], "a book inside all caps must produce no trim"
    kinds = _violation_kinds(plan)
    assert not kinds & {
        "HARD_CAP", "HARD_CAP_NO_TRIM", "HARD_CAP_SUBMIN", "HARD_CAP_UNSELLABLE"
    }


# ── Genuine no-trim case must still raise HARD_CAP_NO_TRIM ──────────────────


def test_unsellable_breach_still_raises_no_trim(frozen_caps, monkeypatch):
    """A breach with NO sellable holding keeps the truthful HARD_CAP_NO_TRIM."""
    monkeypatch.setattr(ta, "SUSPENDED", {"SCBK"})
    book = {
        "cash": 14125.0,
        "positions": [_pos("SCBK", 50, SCBK_PRICE)],  # 15,875 / 30,000 = 52.9%
        "initial_capital": 30000,
    }
    plan = _plan(book)
    assert not [t for t in plan["trades"] if t["side"] == "SELL"]
    kinds = _violation_kinds(plan)
    assert "HARD_CAP" in kinds
    assert "HARD_CAP_NO_TRIM" in kinds  # genuinely no trim — message is true


# ── Razor band: 2dp rounding must not hide a real breach (t_36c85a04) ───────
#
# validate_plan_constraints used to compare the ROUNDED current_pct (2dp)
# against the caps, so a breach in the open band (hard, hard + 0.005pp]
# degraded silently to WARN_CAP. The breach decision now uses the unrounded
# percentage; the display value stays rounded.


def _razor_book(bank_extra: float) -> dict:
    """Book with banking at (45,000 + bank_extra) KES of exactly 100,000 total.

    bank_extra 0.0   -> 45.0000% (exactly at the cap)
    bank_extra 0.1   -> 45.0001% ... 2.1 -> 45.0021% (inside the blind band)
    bank_extra -0.1  -> 44.9999%  (just inside)
    """
    positions = [
        _pos("COOP", 100, 135.0),       # banking 13,500
        _pos("KCB", 100, 143.0),        # banking 14,300
        _pos("SCBK", 50, SCBK_PRICE),   # banking 15,875
        {   # fractional MTM value dials the sector into the razor band
            "symbol": "ABSA",
            "shares": 13,
            "avg_cost": 100.0,
            "current_value": 1325.0 + bank_extra,
        },
        _pos("SCOM", 100, 140.0),       # telecom 14,000
        _pos("KPLC", 1000, 10.0),       # energy  10,000
    ]
    invested = sum(float(p["current_value"]) for p in positions)
    cash = 100000.0 - invested
    return {"cash": cash, "positions": positions, "initial_capital": 100000}


def _banking_pct(book: dict) -> float:
    w = ta.compute_sector_weights(book)
    return w["sectors"]["banking"]["pct"]


def test_razor_breach_raises_hard_cap_not_warn(frozen_caps):
    """45.0021% vs a 45% hard cap -> HARD_CAP (error) + trim + honest skip."""
    book = _razor_book(2.1)
    assert _banking_pct(book) == pytest.approx(45.0021, abs=1e-9)
    plan = _plan(book)
    kinds = _violation_kinds(plan)
    hard = [v for v in plan["violations"] if v.get("kind") == "HARD_CAP"]
    assert hard, f"razor breach must raise HARD_CAP, got kinds {kinds}"
    assert hard[0]["level"] == "error"
    assert hard[0]["current_pct"] == 45.0   # display stays rounded to 2dp
    assert "HARD_CAP_NO_TRIM" not in kinds  # the plan DOES contain a trim
    banking_sells = [
        t for t in plan["trades"]
        if t["side"] == "SELL" and t["sector"] == "banking"
    ]
    assert banking_sells, "razor breach must produce a banking trim"
    # The minimal trim (1 share) is below MIN_TRADE_KES -> the executor skips
    # it with the honest "cannot be corrected this session" message.
    submin = [
        v for v in plan["violations"] if v.get("kind") == "HARD_CAP_SUBMIN"
    ]
    assert submin, f"sub-minimum trim must be named honestly, got kinds {kinds}"
    msg = _min_trade_skip(
        {
            "sector_cap": False,
            "hard_cap_trim": True,
            "sector": "banking",
            "sector_pct": 45.0021,
            "cap_hard_pct": 45.0,
        },
        banking_sells[0]["value"],
    )
    assert msg is not None
    assert "cannot be corrected by a trade this session" in msg


def test_just_inside_cap_no_hard_cap_no_trim(frozen_caps):
    book = _razor_book(-0.1)
    assert _banking_pct(book) == pytest.approx(44.9999, abs=1e-9)
    plan = _plan(book)
    kinds = _violation_kinds(plan)
    assert not kinds & {"HARD_CAP", "HARD_CAP_NO_TRIM", "HARD_CAP_SUBMIN"}
    assert not [
        t for t in plan["trades"]
        if t["side"] == "SELL" and t["sector"] == "banking"
    ], "44.9999% is inside the cap — no trim may be generated"


def test_exactly_at_cap_is_not_a_breach(frozen_caps):
    """Cap comparison is strictly '>' — 45.0000% exactly is NOT a breach.

    Locks the (pre-existing) open-interval semantics explicitly so they stay
    a decision, not an accident.
    """
    book = _razor_book(0.0)
    pct = _banking_pct(book)
    assert pct == pytest.approx(45.0, abs=1e-9)
    plan = _plan(book)
    kinds = _violation_kinds(plan)
    assert not kinds & {"HARD_CAP", "HARD_CAP_NO_TRIM", "HARD_CAP_SUBMIN"}
    assert not [
        t for t in plan["trades"]
        if t["side"] == "SELL" and t["sector"] == "banking"
    ]


def test_validate_uses_unrounded_raw_key(frozen_caps):
    """Unit: validate_plan_constraints must compare the raw, not rounded, pct."""
    targets = {
        "current": {
            "banking": {
                "current_pct": 45.0,        # rounded — used to hide the breach
                "current_pct_raw": 45.0021,  # true value
            }
        }
    }
    violations = ta.validate_plan_constraints({}, targets, [], {})
    kinds = {v["kind"] for v in violations}
    assert "HARD_CAP" in kinds
    assert "WARN_CAP" not in kinds
    # No raw key -> falls back to the rounded value (legacy behaviour)
    targets_legacy = {"current": {"banking": {"current_pct": 45.0}}}
    kinds_legacy = {
        v["kind"] for v in ta.validate_plan_constraints({}, targets_legacy, [], {})
    }
    assert "HARD_CAP" not in kinds_legacy
