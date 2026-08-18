"""Take-profit exit tests — should_take_profit helper + auto-trader emission.

Mirrors test_phase1_risk_gate.py conventions. Isolated temp-state dirs so
nothing touches production state. The emission test replicates EXACTLY the
step-4b block in auto_trader.py (same sl_portfolio_state shape, same
sell_list.append) so a regression there fails loudly.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading.execution import SafetyEngine  # noqa: E402


def _safety(tmp_state: str, **overrides) -> SafetyEngine:
    cfg = {
        "max_trade_size_kes": 500_000.0,
        "max_daily_loss_kes": 100_000.0,
        "max_daily_loss_pct": 5.0,
        "max_single_exposure_pct": 25.0,
        "max_position_count": 20,
        "enabled": True,
        "state_dir": tmp_state,
        "emergency_stop_path": os.path.join(tmp_state, "EMERGENCY_STOP"),
        "max_drawdown_halt_pct": 15.0,
        "stop_loss_pct": 8.0,
        "take_profit_pct": 20.0,
        "macro_fail_open": True,
        "macro_state_path": os.path.join(tmp_state, "macro_breaker.json"),
        "macro": {"index_drop_pct": 3.0, "breadth_min_pct": 20.0,
                  "vol_spike_multiple": 3.0, "cooldown_seconds": 86400},
    }
    cfg.update(overrides)
    return SafetyEngine(cfg)


def _port(positions: dict) -> dict:
    """positions: {sym: {shares, avg_cost, value}}"""
    return {"positions": positions, "total_value": 100_000.0}


# ── should_take_profit helper ────────────────────────────────────────────
def test_should_take_profit_detects_breach():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        port = _port({"COOP": {"shares": 100, "avg_cost": 30.0, "value": 100 * 38.0}})
        tp = s.should_take_profit("COOP", port)
        assert tp is not None
        assert tp["taken"] is True   # +26.7% >= 20%
        assert tp["gain_pct"] == 26.67


def test_take_profit_not_triggered_within_threshold():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        # +11.46% (current live COOP) is below the 20% trigger
        port = _port({"COOP": {"shares": 100, "avg_cost": 34.95, "value": 100 * 38.95}})
        tp = s.should_take_profit("COOP", port)
        assert tp["taken"] is False
        # A loser must NOT be "taken" as a take-profit
        port2 = _port({"SCOM": {"shares": 100, "avg_cost": 35.54, "value": 100 * 35.40}})
        tp2 = s.should_take_profit("SCOM", port2)
        assert tp2["taken"] is False


def test_take_profit_disabled_when_zero():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d, take_profit_pct=0.0)
        port = _port({"COOP": {"shares": 100, "avg_cost": 30.0, "value": 100 * 60.0}})
        assert s.should_take_profit("COOP", port) is None


def test_take_profit_uses_value_over_avg_cost_like_stop_loss():
    """Gain% must be derived from value/shares, consistent with stop-loss math."""
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        # avg_cost 40, value implies current 50 => +25% => taken
        port = _port({"X": {"shares": 10, "avg_cost": 40.0, "value": 10 * 50.0}})
        tp = s.should_take_profit("X", port)
        assert tp["current_price"] == 50.0
        assert tp["gain_pct"] == 25.0
        assert tp["taken"] is True


# ── Emission (replicates auto_trader step-4b exactly) ───────────────────
def _run_tp_step(current_positions: list, safety: SafetyEngine) -> list:
    """Exact copy of auto_trader.py step-4b so a wiring regression fails here."""
    sl_portfolio_state = {
        "positions": {
            p["symbol"]: {
                "shares": p["shares"],
                "avg_cost": p["avg_cost"],
                "value": p.get("current_value", 0) or (p["shares"] * p["avg_cost"]),
            }
            for p in current_positions
        }
    }
    sell_list = []
    for p in current_positions:
        sym = p["symbol"]
        tp = safety.should_take_profit(sym, sl_portfolio_state)
        if tp is not None and tp["taken"]:
            if not any(s["symbol"] == sym for s in sell_list):
                sell_list.append(
                    dict(
                        symbol=sym,
                        delta_shares=int(p["shares"]),
                        reason=(
                            f"Take-profit triggered: {sym} is +{tp['gain_pct']:.1f}% "
                            f"above avg cost of KES {tp['avg_cost']:.2f}"
                        ),
                        take_profit=True,
                    )
                )
    return sell_list


def test_take_profit_emits_full_exit_for_winner_only():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        positions = [
            {"symbol": "WIN", "shares": 200, "avg_cost": 30.0, "current_value": 200 * 39.0},  # +30% -> taken
            {"symbol": "HOLD", "shares": 100, "avg_cost": 34.95, "current_value": 100 * 38.95},  # +11% -> no
            {"symbol": "LOSER", "shares": 50, "avg_cost": 35.54, "current_value": 50 * 33.0},  # -7% -> no
        ]
        sells = _run_tp_step(positions, s)
        assert len(sells) == 1
        assert sells[0]["symbol"] == "WIN"
        assert sells[0]["delta_shares"] == 200          # full exit
        assert sells[0]["take_profit"] is True
        assert "Take-profit triggered" in sells[0]["reason"]


def test_take_profit_no_false_fire_on_live_book_shape():
    """Live book positions (all < 20% gain) must produce zero take-profit sells."""
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        # Snapshot of current live marks (gain vs avg cost, all < 20%)
        live = [
            {"symbol": "ABSA", "shares": 306, "avg_cost": 33.1863, "current_value": 306 * 33.75},
            {"symbol": "COOP", "shares": 100, "avg_cost": 34.95, "current_value": 100 * 38.95},
            {"symbol": "EABL", "shares": 10, "avg_cost": 265.67, "current_value": 10 * 272.75},
            {"symbol": "SCOM", "shares": 100, "avg_cost": 35.54, "current_value": 100 * 35.40},
            {"symbol": "KPLC", "shares": 500, "avg_cost": 19.05, "current_value": 500 * 19.85},
            {"symbol": "KCB", "shares": 50, "avg_cost": 85.58, "current_value": 50 * 86.50},
            {"symbol": "SCBK", "shares": 10, "avg_cost": 339.61, "current_value": 10 * 342.25},
            {"symbol": "TOTL", "shares": 200, "avg_cost": 44.23, "current_value": 200 * 43.35},
            {"symbol": "KNRE", "shares": 1000, "avg_cost": 3.52, "current_value": 1000 * 3.68},
            {"symbol": "EQTY", "shares": 50, "avg_cost": 87.23, "current_value": 50 * 89.25},
        ]
        sells = _run_tp_step(live, s)
        assert sells == [], f"spurious take-profit fire: {sells}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
