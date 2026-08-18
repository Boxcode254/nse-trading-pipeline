"""Regression tests for AUDIT-002 per-share cost-basis handling."""
import os
import tempfile

from trading.auto_trader import _port_state_for_safety
from trading.execution import SafetyEngine


def _safety(tmp_state: str) -> SafetyEngine:
    return SafetyEngine({
        "state_dir": tmp_state,
        "emergency_stop_path": os.path.join(tmp_state, "EMERGENCY_STOP"),
        "macro_state_path": os.path.join(tmp_state, "macro_breaker.json"),
        "macro_fail_open": True,
    })


def test_port_state_preserves_per_share_avg_cost():
    port = _port_state_for_safety([{
        "symbol": "SCOM", "shares": 402, "avg_cost": 35.5379,
        "current_value": 14471.0,
    }])
    assert port["positions"]["SCOM"]["avg_cost"] == 35.5379


def test_live_book_shape_preserves_each_source_basis():
    positions = [
        {"symbol": "SCOM", "shares": 402, "avg_cost": 35.5379, "current_value": 14471.0},
        {"symbol": "KNRE", "shares": 100, "avg_cost": 28.25, "current_value": 2825.0},
    ]
    port = _port_state_for_safety(positions)
    for position in positions:
        assert port["positions"][position["symbol"]]["avg_cost"] == position["avg_cost"]


def test_fixed_basis_stop_loss_percentage_is_sane():
    with tempfile.TemporaryDirectory() as d:
        safety = _safety(d)
        port = _port_state_for_safety([{
            "symbol": "SCOM", "shares": 402, "avg_cost": 35.5379,
            "current_value": 14471.0,
        }])
        result = safety.should_stop_loss("SCOM", port)
        assert result is not None
        assert -100.0 <= result["loss_pct"] <= 100.0
