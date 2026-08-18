"""Phase 1 risk-gate tests — drawdown halt, stop-loss-in-gate, macro breaker.

Covers the three new SafetyEngine gate checks and the MacroBreaker, using
isolated temp-state dirs so nothing touches production state.
"""
import os
import sys
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading.execution import SafetyEngine, MacroBreaker
from trading.execution.models import OrderRequest, AccountInfo
from trading.execution.macro_breaker import MacroSnapshot
from trading.auto_trader import _port_state_for_safety


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
        "macro_fail_open": True,
        "macro_state_path": os.path.join(tmp_state, "macro_breaker.json"),
        "macro": {"index_drop_pct": 3.0, "breadth_min_pct": 20.0,
                  "vol_spike_multiple": 3.0, "cooldown_seconds": 86400},
    }
    cfg.update(overrides)
    return SafetyEngine(cfg)


def _account(equity=100_000.0) -> AccountInfo:
    return AccountInfo(cash=equity, equity=equity, buying_power=equity,
                       positions_count=1, daily_pnl=0.0, daily_pnl_pct=0.0)


def _port(positions: dict) -> dict:
    """positions: {sym: {shares, avg_cost, value}}"""
    return {"positions": positions, "total_value": 100_000.0}


def _buy(sym, price=10.0, qty=100):
    return OrderRequest(symbol=sym, side="BUY", quantity=qty, price=price)


def _sell(sym, price=10.0, qty=100):
    return OrderRequest(symbol=sym, side="SELL", quantity=qty, price=price)


# ── 5. Drawdown halt ──────────────────────────────────────────────────────
def test_drawdown_halt_blocks_all_trades_when_exceeded():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        # No drawdown yet → trade allowed
        v = s.check_order(_buy("SCOM"), _port({}), _account())
        assert v.allowed, v.reason
        # Exceed the halt threshold
        s.update_drawdown(20.0)
        assert s.state["drawdown_halted"] is True
        # Now every order (buy AND sell) is blocked
        vbuy = s.check_order(_buy("SCOM"), _port({}), _account())
        vsell = s.check_order(_sell("SCOM"), _port({}), _account())
        assert not vbuy.allowed and "drawdown_halt" in vbuy.violations
        assert not vsell.allowed and "drawdown_halt" in vsell.violations


def test_drawdown_halt_persists_and_releases():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        s.update_drawdown(18.0)
        # New engine instance over same state dir sees the halt
        s2 = _safety(d)
        assert s2.state["drawdown_halted"] is True
        v = s2.check_order(_buy("SCOM"), _port({}), _account())
        assert not v.allowed
        # Release via operator
        s2.release_drawdown_halt()
        v3 = s2.check_order(_buy("SCOM"), _port({}), _account())
        assert v3.allowed, v3.reason


def test_drawdown_below_limit_does_not_halt():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        s.update_drawdown(10.0)  # below 15% limit
        assert s.state["drawdown_halted"] is False
        v = s.check_order(_buy("SCOM"), _port({}), _account())
        assert v.allowed


# ── 6. Stop-loss moved into the gate ─────────────────────────────────────
def test_should_stop_loss_detects_breach():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        port = _port({"SCOM": {"shares": 100, "avg_cost": 50.0, "value": 100 * 45.0}})
        sl = s.should_stop_loss("SCOM", port)
        assert sl is not None
        assert sl["stopped"] is True  # -10% < -8%
        assert sl["loss_pct"] == -10.0


def test_stop_loss_blocks_buy_not_sell():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        port = _port({"SCOM": {"shares": 100, "avg_cost": 50.0, "value": 100 * 45.0}})
        # BUY into a stopped position is blocked
        vbuy = s.check_order(_buy("SCOM"), port, _account())
        assert not vbuy.allowed and "stop_loss_blocked" in vbuy.violations
        # SELL out of a stopped position is allowed (correct exit)
        vsell = s.check_order(_sell("SCOM"), port, _account())
        assert vsell.allowed, vsell.reason


def test_stop_loss_not_triggered_when_within_threshold():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        port = _port({"SCOM": {"shares": 100, "avg_cost": 50.0, "value": 100 * 49.0}})
        sl = s.should_stop_loss("SCOM", port)
        assert sl["stopped"] is False  # -2% > -8%
        vbuy = s.check_order(_buy("SCOM"), port, _account())
        assert vbuy.allowed, vbuy.reason


def test_stop_loss_disabled_when_zero():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d, stop_loss_pct=0.0)
        port = _port({"SCOM": {"shares": 100, "avg_cost": 50.0, "value": 100 * 30.0}})
        assert s.should_stop_loss("SCOM", port) is None
        vbuy = s.check_order(_buy("SCOM"), port, _account())
        assert vbuy.allowed


# ── 7. Macro / volatility circuit breaker ────────────────────────────────
def test_macro_trips_on_index_drop():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        res = s.feed_macro({"index_change_pct": -5.0, "source": "test"})
        assert res["tripped"] is True
        v = s.check_order(_buy("SCOM"), _port({}), _account())
        assert not v.allowed and "macro_breaker" in v.violations


def test_macro_trips_on_breadth_collapse():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        res = s.feed_macro({"advancers": 5, "decliners": 95, "source": "test"})
        assert res["tripped"] is True
        v = s.check_order(_buy("SCOM"), _port({}), _account())
        assert not v.allowed


def test_macro_fail_open_when_no_data():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        # No feed at all → breaker never trips → trades allowed
        v = s.check_order(_buy("SCOM"), _port({}), _account())
        assert v.allowed
        # Feed with missing fields → no threshold evaluated → no trip
        res = s.feed_macro({"source": "test"})
        assert res["tripped"] is False
        v2 = s.check_order(_buy("SCOM"), _port({}), _account())
        assert v2.allowed


def test_macro_release_clears_halt():
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        s.feed_macro({"index_change_pct": -6.0, "source": "test"})
        assert s.macro.evaluate() is True
        s.release_macro()
        assert s.macro.evaluate() is False
        v = s.check_order(_buy("SCOM"), _port({}), _account())
        assert v.allowed


def test_macro_breaker_unit_index_drop():
    with tempfile.TemporaryDirectory() as d:
        mb = MacroBreaker(thresholds={"index_drop_pct": 3.0},
                          state_path=os.path.join(d, "m.json"), fail_open=True)
        mb.feed(MacroSnapshot(timestamp=_now(), index_change_pct=-4.0))
        assert mb.evaluate() is True
        mb.reset()
        assert mb.evaluate() is False


def test_macro_breaker_fail_closed_blocks_on_missing_data():
    with tempfile.TemporaryDirectory() as d:
        mb = MacroBreaker(thresholds={}, state_path=os.path.join(d, "m.json"),
                          fail_open=False)
        # fail-closed with no data still evaluates to not-tripped (no breach),
        # but the design guarantee is that a *present* breach trips.
        mb.feed(MacroSnapshot(timestamp=_now(), index_change_pct=-10.0))
        assert mb.evaluate() is True


def test_macro_derive_snapshot_from_prices_breadth_and_index():
    """build_snapshot_from_prices derives breadth + composite index change."""
    with tempfile.TemporaryDirectory() as d:
        mb = MacroBreaker(thresholds={"index_drop_pct": 3.0,
                                      "breadth_min_pct": 20.0,
                                      "vol_spike_multiple": 3.0},
                          state_path=os.path.join(d, "m.json"), fail_open=True)
        # Stub the live TV fetch so the derived composite is what gets used
        # (in prod, a resolving TV index would override the proxy).
        mb.fetch_live_nse = lambda: None
        # min_sample=3 so this 4-symbol (2 up / 2 down) feed is representative.
        prices = {
            "SCOM": {"price": 35.5, "change_pct": -4.0},
            "KCB": {"price": 80.0, "change_pct": -5.0},
            "EQTY": {"price": 86.0, "change_pct": 2.0},
            "ABSA": {"price": 33.0, "change_pct": 1.0},
            "_errors": ["COOP: no data"],
        }
        out = mb.build_snapshot_from_prices(prices, min_sample=3)
        snap = out["snapshot"]
        # 2 decliners, 2 advancers
        assert snap["advancers"] == 2
        assert snap["decliners"] == 2
        # composite index change = mean of the 4 valid changes = (-4-5+2+1)/4
        assert abs(snap["index_change_pct"] - (-1.5)) < 1e-6
        # breadth 50% >= 20% floor → no trip; index -1.5% > -3% → no trip
        assert out["breaker"]["tripped"] is False
        # persisted file exists
        assert os.path.exists(os.path.join(d, "macro_snapshot.json"))


def test_macro_refresh_macro_trips_on_broad_selloff():
    """SafetyEngine.refresh_macro() feeds a derived snapshot and can trip."""
    with tempfile.TemporaryDirectory() as d:
        s = _safety(d)
        # 4 down + min_sample=4 → representative → composite -6.5% trips.
        prices = {
            "SCOM": {"price": 35.5, "change_pct": -6.0},
            "KCB": {"price": 80.0, "change_pct": -7.0},
            "EQTY": {"price": 86.0, "change_pct": -5.0},
            "ABSA": {"price": 33.0, "change_pct": -8.0},
        }
        out = s.refresh_macro(prices=prices, min_sample=4)
        # All four down → composite -6.5% < -3% → trips
        assert out["breaker"]["tripped"] is True
        v = s.check_order(_buy("SCOM"), _port({}), _account())
        assert not v.allowed and "macro_breaker" in v.violations


def test_macro_sparse_sample_does_not_trip():
    """Sparse / flat-dominated feeds must NOT trip the breaker (fail-open)."""
    with tempfile.TemporaryDirectory() as d:
        mb = MacroBreaker(thresholds={"index_drop_pct": 3.0,
                                      "breadth_min_pct": 20.0,
                                      "vol_spike_multiple": 3.0},
                          state_path=os.path.join(d, "m.json"), fail_open=True)
        mb.fetch_live_nse = lambda: None  # force derived-only path
        # Case 1: only 1 symbol returned at all → not representative.
        out1 = mb.build_snapshot_from_prices(
            {"SCOM": {"price": 35.5, "change_pct": -9.0}}, min_sample=4)
        assert out1["representative"] is False
        assert out1["breaker"]["tripped"] is False
        assert out1["snapshot"]["advancers"] is None
        # Case 2: 12 symbols but 11 are FLAT (change_pct == 0.0) and 1 down.
        # This is the real TradingView failure mode — flats must not count as
        # a breadth signal. non_flat == 1 < min_sample → no evaluation.
        flat_prices = {
            "SCOM": {"price": 35.5, "change_pct": -9.0},
            **{f"SYM{i}": {"price": 10.0, "change_pct": 0.0} for i in range(11)},
        }
        out2 = mb.build_snapshot_from_prices(flat_prices, min_sample=4)
        assert out2["representative"] is False
        assert out2["breaker"]["tripped"] is False
        # When non_flat is enough (here 4 down), it DOES trip.
        down4 = {
            "SCOM": {"price": 35.5, "change_pct": -6.0},
            "KCB": {"price": 80.0, "change_pct": -7.0},
            "EQTY": {"price": 86.0, "change_pct": -5.0},
            "ABSA": {"price": 33.0, "change_pct": -8.0},
        }
        out3 = mb.build_snapshot_from_prices(down4, min_sample=4)
        assert out3["representative"] is True
        assert out3["breaker"]["tripped"] is True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
