"""Regression tests for AUDIT-006 MTM refresh authority and field scope."""
from __future__ import annotations

import copy
import datetime
import json

from trading.price_source import apply_authoritative_prices


def _state() -> dict:
    return {
        "cash": 1234.5,
        "initial_capital": 100000.0,
        "created_at": "2026-08-01T00:00:00+03:00",
        "updated_at": "2026-08-05T10:00:00+03:00",
        "positions": [
            {"symbol": "ABSA", "shares": 10, "avg_cost": 30.0,
             "total_cost": 300.0, "current_value": 301.0},
            {"symbol": "EQTY", "shares": 5, "avg_cost": 80.0,
             "total_cost": 400.0, "current_value": 401.0},
        ],
    }


def test_axys_refresh_updates_values_only(tmp_path, monkeypatch):
    """Official closes update stale marks without changing book fields."""
    state = _state()
    before = copy.deepcopy(state)
    portfolio = tmp_path / "portfolio"
    data = tmp_path / "data"
    portfolio.mkdir()
    data.mkdir()
    today = datetime.date.today().isoformat()
    (portfolio / f"axys_closes_{today}.json").write_text(
        json.dumps({"axys": {"ABSA": 34.35, "EQTY": 92.0}})
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    result = apply_authoritative_prices(state, str(portfolio), previous=before)

    assert result.sources == {"ABSA": "axys", "EQTY": "axys"}
    assert state["positions"][0]["current_value"] == 343.5
    assert state["positions"][1]["current_value"] == 460.0
    for key in ("cash", "initial_capital", "created_at", "updated_at"):
        assert state[key] == before[key]
    for old, new in zip(before["positions"], state["positions"]):
        for key in ("symbol", "shares", "avg_cost", "total_cost"):
            assert new[key] == old[key]


def test_refresh_falls_back_without_axys(tmp_path, monkeypatch):
    """Missing official file remains fail-open via feed, then CSV cache."""
    state = _state()
    portfolio = tmp_path / "portfolio"
    data = tmp_path / "data"
    portfolio.mkdir()
    data.mkdir()
    (portfolio / "mtm_state.json").write_text(
        json.dumps({"positions": [{"symbol": "ABSA", "live_price": 33.5}]})
    )
    (data / "nse_EQTY.csv").write_text("date,close\n2026-08-01,91.25\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    result = apply_authoritative_prices(state, str(portfolio), previous=state)

    assert result.sources == {"ABSA": "feed", "EQTY": "csv"}
    assert state["positions"][0]["current_value"] == 335.0
    assert state["positions"][1]["current_value"] == 456.25


def test_refresh_uncovered_symbol_preserves_previous_mark(tmp_path):
    state = _state()
    before = copy.deepcopy(state)
    portfolio = tmp_path / "portfolio"
    portfolio.mkdir()

    result = apply_authoritative_prices(state, str(portfolio), previous=before)

    assert result.sources == {}
    assert state == before


if __name__ == "__main__":
    raise SystemExit("run with pytest")
