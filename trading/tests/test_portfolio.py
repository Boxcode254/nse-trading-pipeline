"""Tests for the Paper Portfolio Manager (Phase 4).

Covers:
- Engine: data model, state I/O, buy/sell/snapshot, edge cases
- Service facade: returns plain dicts
- CLI: every subcommand, --json output, --force reset, error paths

Run:
    cd ~/.trading && .venv/bin/python -m pytest trading/tests/test_portfolio.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

# Isolate HOME so tests never touch the real ~/.trading. Each test that
# touches the filesystem creates its own tmpdir and re-points HOME.
TMP_HOME = tempfile.mkdtemp(prefix="trading-pf-test-")
os.environ["HOME"] = TMP_HOME


def _isolated_home(test_name: str) -> str:
    """Per-test HOME so engine state never bleeds across tests."""
    d = tempfile.mkdtemp(prefix=f"pf-{test_name}-")
    os.environ["HOME"] = d
    # Clear any cached config-derived paths (e.g. default portfolio dir is
    # built from HOME at import time, but engine takes it dynamically).
    return d


# Decorator: every test runs in a fresh isolated HOME so state never bleeds.
def _isolated(fn):
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _isolated_home(fn.__name__)
        return fn(*args, **kwargs)
    return wrapper


# Apply isolation to every test_* function defined below.
# (populated at module bottom so all test functions exist)


# ── Engine tests ─────────────────────────────────────────────────────────
def test_engine_init_creates_state() -> None:
    """init_portfolio creates state.json with the right starting values."""
    from trading.portfolio import engine as pf
    state = pf.init_portfolio(capital=100_000.0)
    assert state.cash == 100_000.0
    assert state.positions == []
    assert state.initial_capital == 100_000.0
    assert pf.portfolio_exists()
    # Files exist
    base = Path(pf._default_portfolio_dir())
    assert (base / "state.json").exists()
    assert (base / "transactions.json").exists()
    assert (base / "snapshots.json").exists()
    assert (base / "benchmark.json").exists()


def test_engine_init_rejects_duplicate_without_force() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0)
    try:
        pf.init_portfolio(capital=50_000.0)
    except pf.PortfolioExistsError:
        return
    raise AssertionError("expected PortfolioExistsError on duplicate init")


def test_engine_init_force_resets() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0)
    pf.buy("SCOM", 100, 42.5, reason="init test")
    assert pf.load_state().cash < 100_000.0
    # --force wipes everything
    state = pf.init_portfolio(capital=200_000.0, force=True)
    assert state.cash == 200_000.0
    assert state.positions == []
    assert pf.load_transactions() == []


def test_engine_init_rejects_zero_capital() -> None:
    from trading.portfolio import engine as pf
    import pytest
    with pytest.raises(pf.PortfolioError):
        pf.init_portfolio(capital=0.0)


def test_buy_records_transaction_and_updates_state() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    state, txn = pf.buy("SCOM", 100, 42.5, reason="Strong buy")
    expected_fee = max(round(100 * 42.5 * pf.TRANSACTION_FEE_PCT, 2), pf.FEE_MIN)
    assert state.cash == round(100_000.0 - 100 * 42.5 - expected_fee, 2)
    assert txn.action == "BUY"
    assert txn.symbol == "SCOM"
    assert txn.shares == 100
    assert txn.price == 42.5
    assert txn.total == 4250.0
    assert txn.fee == expected_fee
    assert txn.net_cash_delta == round(-(4250.0 + expected_fee), 2)
    assert txn.realised_pnl is None
    assert state.positions[0].shares == 100
    assert state.positions[0].avg_cost == 42.5


def test_buy_weighted_average_cost_basis() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("SCOM", 100, 40.0)
    state, _ = pf.buy("SCOM", 100, 50.0)
    # Weighted avg = (100*40 + 100*50) / 200 = 45.0
    assert state.positions[0].shares == 200
    assert state.positions[0].avg_cost == 45.0
    assert state.positions[0].total_cost == 9000.0


def test_buy_rejects_insufficient_cash() -> None:
    from trading.portfolio import engine as pf
    import pytest
    pf.init_portfolio(capital=1000.0, force=True)
    with pytest.raises(pf.InsufficientCashError):
        pf.buy("SCOM", 100, 42.5)


def test_buy_rejects_zero_or_negative_shares() -> None:
    from trading.portfolio import engine as pf
    import pytest
    pf.init_portfolio(capital=100_000.0, force=True)
    with pytest.raises(pf.PortfolioError):
        pf.buy("SCOM", 0, 42.5)
    with pytest.raises(pf.PortfolioError):
        pf.buy("SCOM", -5, 42.5)


def test_sell_partial_keeps_position() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("SCOM", 100, 40.0)
    state, txn = pf.sell("SCOM", 50, 50.0, reason="trim")
    assert txn.action == "SELL"
    assert txn.shares == 50
    assert txn.realised_pnl == round((50.0 - 40.0) * 50, 2)  # +500
    assert state.positions[0].shares == 50
    # Cost basis reduced proportionally
    assert state.positions[0].total_cost == round(100 * 40.0 * 0.5, 2)


def test_sell_all_removes_position() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("SCOM", 100, 40.0)
    state, txn = pf.sell("SCOM", None, 50.0)
    assert state.positions == []
    assert txn.shares == 100


def test_sell_rejects_unknown_position() -> None:
    from trading.portfolio import engine as pf
    import pytest
    pf.init_portfolio(capital=100_000.0, force=True)
    with pytest.raises(pf.UnknownPositionError):
        pf.sell("KCB", 10, 8.0)


def test_sell_rejects_more_shares_than_held() -> None:
    from trading.portfolio import engine as pf
    import pytest
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("KCB", 50, 8.0)
    with pytest.raises(pf.InsufficientSharesError):
        pf.sell("KCB", 100, 9.0)


def test_snapshot_computes_total_and_drawdown() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("SCOM", 100, 40.0)
    snap = pf.take_snapshot(prices={"SCOM": 50.0})
    # Holdings = 100 * 50 = 5000; cash reduced by ~4000+; total > 100000
    assert snap.holdings_value == 5000.0
    assert snap.total_value > 100_000.0
    assert snap.total_return_pct > 0.0
    # Drawdown is 0 because this is the first real snapshot
    assert snap.drawdown_pct == 0.0


def test_snapshot_drawdown_increases_on_loss() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("SCOM", 100, 50.0)
    pf.take_snapshot(prices={"SCOM": 60.0})  # peak = 106000
    snap2 = pf.take_snapshot(prices={"SCOM": 40.0})  # 100*40 + cash < peak
    assert snap2.drawdown_pct > 0.0
    # max_drawdown is stored on state
    state = pf.load_state()
    assert state.max_drawdown_pct > 0.0


def test_compute_drawdown_zero_at_peak() -> None:
    from trading.portfolio import engine as pf
    snaps = [
        pf.Snapshot("t1", 100000, 0, 100000, 0, 0, 0, 100000),
        pf.Snapshot("t2", 90000, 0, 90000, 0, 0, 0, 90000),
        pf.Snapshot("t3", 95000, 0, 95000, 0, 0, 0, 95000),  # peak still 100k
        pf.Snapshot("t4", 110000, 0, 110000, 0, 0, 0, 110000),  # new peak
    ]
    dds = pf.compute_drawdown(snaps)
    assert dds[0] == 0.0
    assert dds[1] == 10.0
    # At t3, peak is still 100k, so dd = (100-95)/100 = 5
    assert dds[2] == 5.0
    # At t4, peak updates to 110k, value is 110k, dd = 0
    assert dds[3] == 0.0


def test_compute_holdings_value_uses_avg_cost_as_fallback() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("SCOM", 100, 42.5)
    state = pf.load_state()
    # No price provided → falls back to avg_cost (no crash, value reconciles)
    holdings, rows = pf.compute_holdings_value(state, prices={})
    assert holdings == round(100 * 42.5, 2)
    assert rows[0]["last_price"] == 42.5


def test_transactions_log_is_append_only() -> None:
    """Once written, the transaction log should not be modified in place."""
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("SCOM", 100, 40.0)
    pf.sell("SCOM", 50, 50.0)
    pf.buy("KCB", 50, 8.0)
    log = pf.load_transactions()
    actions = [t.action for t in log]
    assert actions == ["BUY", "SELL", "BUY"]
    symbols = [t.symbol for t in log]
    assert symbols == ["SCOM", "SCOM", "KCB"]


def test_snapshots_to_csv_format() -> None:
    from trading.portfolio import engine as pf
    pf.init_portfolio(capital=100_000.0, force=True)
    pf.buy("SCOM", 100, 40.0)
    pf.take_snapshot(prices={"SCOM": 50.0})
    csv = pf.snapshots_to_csv(pf.load_snapshots())
    assert csv.startswith("timestamp,cash,holdings_value,total_value")
    assert "drawdown_pct" in csv
    # At least 2 rows (header + 2 snapshots)
    assert len(csv.strip().split("\n")) >= 3


# ── Service facade tests ─────────────────────────────────────────────────
def test_service_init_and_show() -> None:
    from trading.services import portfolio as svc
    res = svc.init(capital=50_000.0)
    assert res["status"] == "initialised"
    assert res["cash"] == 50_000.0
    res = svc.show()
    assert res["status"] == "ok"
    assert res["total_value"] == 50_000.0
    assert res["positions"] == []


def test_service_show_before_init_returns_not_initialised() -> None:
    from trading.services import portfolio as svc
    res = svc.show()
    assert res["status"] == "not_initialised"


def test_service_buy_and_sell_with_overridden_price() -> None:
    from trading.services import portfolio as svc
    svc.init(capital=100_000.0, force=True)
    res = svc.buy("SCOM", 100, price=40.0, reason="test")
    assert res["status"] == "filled"
    assert res["transaction"]["shares"] == 100
    res = svc.sell("SCOM", shares=50, price=55.0, reason="trim")
    assert res["status"] == "filled"
    assert res["transaction"]["realised_pnl"] == round((55.0 - 40.0) * 50, 2)


def test_service_snapshot_appends_series() -> None:
    from trading.services import portfolio as svc
    svc.init(capital=100_000.0, force=True)
    svc.buy("SCOM", 100, price=40.0)
    res = svc.snapshot()
    assert res["status"] == "ok"
    res = svc.history(days=30)
    assert res["count"] >= 1


def test_service_decisions_filters() -> None:
    from trading.services import portfolio as svc
    svc.init(capital=100_000.0, force=True)
    svc.buy("SCOM", 100, price=40.0)
    svc.buy("KCB", 50, price=8.0)
    res = svc.decisions()
    assert res["count"] == 2
    res = svc.decisions(symbol="SCOM")
    assert res["count"] == 1
    res = svc.decisions(last=1)
    assert res["count"] == 1
    assert res["transactions"][0]["symbol"] == "KCB"


# ── CLI tests ────────────────────────────────────────────────────────────
def test_cli_portfolio_subgroup_registered() -> None:
    """portfolio must now be a subcommand group with 7 children."""
    from trading.cli.main import app
    sub_groups = list(getattr(app, "registered_groups", []))
    portfolio_grp = next(
        (g for g in sub_groups if getattr(g, "name", None) == "portfolio"), None
    )
    assert portfolio_grp is not None, "portfolio sub-app not registered"
    names = {cmd.name for cmd in portfolio_grp.typer_instance.registered_commands}
    assert {"init", "show", "buy", "sell", "snapshot", "history", "decisions"} <= names


def test_cli_init_creates_portfolio() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    res = runner.invoke(app, ["portfolio", "init", "--capital", "75000", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["capital"] == 75_000.0
    assert data["cash"] == 75_000.0


def test_cli_show_after_init() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "50000", "--json"])
    res = runner.invoke(app, ["portfolio", "show", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["total_value"] == 50_000.0


def test_cli_buy_with_explicit_price() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "100000", "--force", "--json"])
    res = runner.invoke(app, [
        "portfolio", "buy", "SCOM", "--shares", "100", "--price", "42.50", "--json",
    ])
    assert res.exit_code == 0, f"buy failed: {res.output}"
    data = json.loads(res.output)
    assert data["side"] == "BUY"
    assert data["shares"] == 100
    assert data["price"] == 42.5


def test_cli_buy_rejects_overdraw() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "1000", "--force", "--json"])
    res = runner.invoke(app, [
        "portfolio", "buy", "SCOM", "--shares", "100", "--price", "42.50", "--json",
    ])
    assert res.exit_code != 0
    assert "insufficient" in res.output.lower()


def test_cli_sell_partial_and_full() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "100000", "--force", "--json"])
    runner.invoke(app, [
        "portfolio", "buy", "SCOM", "--shares", "100", "--price", "40.00", "--json",
    ])
    res = runner.invoke(app, [
        "portfolio", "sell", "SCOM", "--shares", "40", "--price", "55.00", "--json",
    ])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["shares"] == 40
    # Realised PnL = (55 - 40) * 40 = 600
    assert data["realised_pnl"] == 600.0
    # Remaining position
    assert data["remaining_position"]["shares"] == 60
    # Sell all (omit --shares)
    res = runner.invoke(app, [
        "portfolio", "sell", "SCOM", "--price", "60.00", "--json",
    ])
    assert res.exit_code == 0
    assert json.loads(res.output)["remaining_position"] is None


def test_cli_sell_unknown_symbol_fails() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "100000", "--force", "--json"])
    res = runner.invoke(app, [
        "portfolio", "sell", "KCB", "--shares", "10", "--price", "8.0", "--json",
    ])
    assert res.exit_code != 0


def test_cli_snapshot_runs() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "100000", "--force", "--json"])
    res = runner.invoke(app, ["portfolio", "snapshot", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "ok"
    assert data["total_value"] == 100_000.0


def test_cli_decisions_lists_trades() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "100000", "--force", "--json"])
    runner.invoke(app, ["portfolio", "buy", "SCOM", "--shares", "100", "--price", "40.0", "--json"])
    runner.invoke(app, ["portfolio", "buy", "KCB", "--shares", "50", "--price", "8.0", "--json"])
    res = runner.invoke(app, ["portfolio", "decisions", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["count"] == 2
    res = runner.invoke(app, ["portfolio", "decisions", "--last", "1", "--json"])
    data = json.loads(res.output)
    assert data["count"] == 1
    assert data["transactions"][0]["symbol"] == "KCB"
    res = runner.invoke(app, ["portfolio", "decisions", "--symbol", "SCOM", "--json"])
    data = json.loads(res.output)
    assert data["count"] == 1
    assert data["transactions"][0]["symbol"] == "SCOM"


def test_cli_history_json_and_csv() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "100000", "--force", "--json"])
    runner.invoke(app, ["portfolio", "buy", "SCOM", "--shares", "100", "--price", "40.0", "--json"])
    runner.invoke(app, ["portfolio", "snapshot", "--json"])
    res = runner.invoke(app, ["portfolio", "history", "--days", "30", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert "snapshots" in data
    assert data["count"] >= 1
    res = runner.invoke(app, ["portfolio", "history", "--csv"])
    assert res.exit_code == 0
    assert "timestamp,cash,holdings_value" in res.output


def test_cli_init_force_resets_existing_portfolio() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "100000", "--json"])
    runner.invoke(app, ["portfolio", "buy", "SCOM", "--shares", "100", "--price", "40.0", "--json"])
    res = runner.invoke(app, ["portfolio", "init", "--force", "--capital", "200000", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "reset"
    assert data["capital"] == 200_000.0
    res = runner.invoke(app, ["portfolio", "decisions", "--json"])
    assert json.loads(res.output)["count"] == 0


def test_cli_duplicate_init_without_force_fails() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    runner.invoke(app, ["portfolio", "init", "--capital", "100000", "--json"])
    res = runner.invoke(app, ["portfolio", "init", "--capital", "50000", "--json"])
    assert res.exit_code != 0
    assert "exists" in res.output.lower() or "force" in res.output.lower()


def test_cli_show_before_init_fails() -> None:
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    res = runner.invoke(app, ["portfolio", "show", "--json"])
    assert res.exit_code != 0
    assert "no portfolio" in res.output.lower() or "init" in res.output.lower()


# Apply isolation to every test_* function defined above.
import types as _types
for _name, _obj in list(globals().items()):
    if _name.startswith("test_") and isinstance(_obj, _types.FunctionType) and not getattr(_obj, "_isolated", False):
        globals()[_name] = _isolated(_obj)
        globals()[_name]._isolated = True  # type: ignore[attr-defined]
del _types, _name, _obj


if __name__ == "__main__":
    # Plain script mode: collect + invoke each test manually
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR   {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed} test(s) failed")
        raise SystemExit(1)
    print(f"\nAll {len(tests)} tests passed")
