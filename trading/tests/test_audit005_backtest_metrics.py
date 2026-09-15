"""Regression tests for AUDIT-005 backtest win metrics."""

import json
from pathlib import Path

import pytest

from trading.backtest.live_strategy_backtest import (
    Trade,
    _compute_win_metrics as compute_win_metrics,
    _compute_live_ledger_metrics,
    _load_live_ledger,
)


_EMPTY = ([], [], [])


def _trade(side, price, shares=1, fee=1.0, slippage=0.0):
    gross = price * shares
    return Trade(
        date="2026-01-01",
        symbol="ABC",
        side=side,
        shares=shares,
        price=price,
        gross=gross,
        fee=fee,
        slippage=slippage,
        net_cash=0.0,
        reason="test",
    )


def _fixture_ledger():
    """A temporary ledger with known wins/losses (never the live book).

    Fee-inclusive realised_pnl as produced by the portfolio engine:
      * SELL SCOM  +432.50  -> win
      * SELL KCB   -120.00  -> loss
      * SELL ABSA     0.00  -> loss (zero is a loss, not a win)
    """
    return [
        {"action": "BUY", "symbol": "SCOM", "shares": 100, "price": 20.0,
         "fee": 30.0, "net_cash_delta": -2030.0, "realised_pnl": None,
         "timestamp": "2026-01-02T10:00:00+03:00"},
        {"action": "SELL", "symbol": "SCOM", "shares": 100, "price": 25.0,
         "fee": 37.5, "net_cash_delta": 2462.5, "realised_pnl": 432.5,
         "timestamp": "2026-01-09T10:00:00+03:00"},
        {"action": "BUY", "symbol": "KCB", "shares": 50, "price": 40.0,
         "fee": 30.0, "net_cash_delta": -2030.0, "realised_pnl": None,
         "timestamp": "2026-01-12T10:00:00+03:00"},
        {"action": "SELL", "symbol": "KCB", "shares": 50, "price": 37.4,
         "fee": 28.05, "net_cash_delta": 1841.95, "realised_pnl": -120.0,
         "timestamp": "2026-01-19T10:00:00+03:00"},
        {"action": "BUY", "symbol": "ABSA", "shares": 40, "price": 15.0,
         "fee": 9.0, "net_cash_delta": -609.0, "realised_pnl": None,
         "timestamp": "2026-01-20T10:00:00+03:00"},
        {"action": "SELL", "symbol": "ABSA", "shares": 40, "price": 15.0,
         "fee": 9.0, "net_cash_delta": 591.0, "realised_pnl": 0.0,
         "timestamp": "2026-01-27T10:00:00+03:00"},
    ]


def test_win_rate_and_profit_factor_use_fee_inclusive_round_trips():
    trades = [
        _trade("BUY", 100), _trade("SELL", 110),  # +8 after both fees
        _trade("BUY", 100), _trade("SELL", 105),  # +3
        _trade("BUY", 100), _trade("SELL", 98),   # -3
    ]

    win_rate, profit_factor = compute_win_metrics(*((trades,) + _EMPTY))

    assert win_rate == pytest.approx(200 / 3)
    assert profit_factor == pytest.approx(11 / 4)
    assert profit_factor < 10


def test_all_losses_have_zero_profit_factor():
    trades = [_trade("BUY", 100), _trade("SELL", 90)]

    win_rate, profit_factor = compute_win_metrics(*((trades,) + _EMPTY))

    assert win_rate == 0.0
    assert profit_factor == 0.0


def test_all_wins_have_undefined_profit_factor_not_fake_ratio():
    trades = [_trade("BUY", 100), _trade("SELL", 110)]

    win_rate, profit_factor = compute_win_metrics(*((trades,) + _EMPTY))

    assert win_rate == 100.0
    assert profit_factor is None


def test_buy_fee_is_included_in_basis_for_break_even_sell():
    trades = [_trade("BUY", 100, fee=5), _trade("SELL", 105, fee=0)]

    win_rate, profit_factor = compute_win_metrics(*((trades,) + _EMPTY))

    assert win_rate == 0.0
    assert profit_factor is None


def test_live_ledger_metrics_use_realised_pnl_and_include_losses():
    ledger = [
        {"action": "SELL", "realised_pnl": 100.0, "fee": 5.0},
        {"action": "SELL", "realised_pnl": -40.0, "fee": 5.0},
        {"action": "BUY", "realised_pnl": None, "fee": 10.0},
    ]

    metrics = _compute_live_ledger_metrics(ledger)

    assert metrics["sell_count"] == 2
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert metrics["win_rate_pct"] == pytest.approx(50.0)
    assert metrics["profit_factor"] == pytest.approx(2.5)


def test_live_ledger_metrics_treat_zero_as_a_loss():
    metrics = _compute_live_ledger_metrics([
        {"action": "SELL", "realised_pnl": 0.0},
    ])

    assert metrics["wins"] == 0
    assert metrics["losses"] == 1
    assert metrics["win_rate_pct"] == 0.0
    assert metrics["profit_factor"] is None


def test_live_ledger_loader_reads_fixture_ledger(monkeypatch, tmp_path):
    """TP-009: the loader/parser is deterministic — point HOME at a fixture.

    The canonical ledger is MUTABLE production state (it grew 82 -> 94 during
    this remediation), so the parser must be exercised against a temporary
    ledger with known wins/losses rather than the live book.
    """
    fixture = _fixture_ledger()
    pdir = tmp_path / ".trading" / "portfolio"
    pdir.mkdir(parents=True)
    (pdir / "transactions.json").write_text(json.dumps(fixture))
    monkeypatch.setenv("HOME", str(tmp_path))

    ledger, source = _load_live_ledger()

    assert source == str(pdir / "transactions.json")
    assert len(ledger) == len(fixture)

    metrics = _compute_live_ledger_metrics(ledger)
    assert metrics["sell_count"] == 3
    assert metrics["closed_with_pnl"] == 3
    assert metrics["wins"] == 1
    assert metrics["losses"] == 2
    assert metrics["win_rate_pct"] == pytest.approx(100 / 3)
    assert metrics["profit_factor"] == pytest.approx(432.5 / 120.0)
    assert metrics["realised_pnl_total"] == pytest.approx(312.5)


def test_live_ledger_loader_handles_missing_and_malformed_fixture(monkeypatch, tmp_path):
    """A missing or corrupt ledger degrades to ([], None) — never raises."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _load_live_ledger() == ([], None)

    pdir = tmp_path / ".trading" / "portfolio"
    pdir.mkdir(parents=True)
    (pdir / "transactions.json").write_text("{not json")
    assert _load_live_ledger() == ([], None)

    (pdir / "transactions.json").write_text(json.dumps({"not": "a list"}))
    assert _load_live_ledger() == ([], None)


@pytest.mark.live_data
def test_live_ledger_invariants_read_only(monkeypatch):
    """Read-only live smoke: the ledger PARSES and stays internally coherent.

    Deliberately asserts no historical counts (the book legitimately grows):
    shape of every entry, a FIFO replay that never goes short, and metric
    self-consistency. Deselect with ``-m "not live_data"``.
    """
    # Other test modules poison HOME at import time. Restore the account home
    # for this integration check so Path.home() resolves the canonical ledger.
    import os
    import pwd

    real_home = pwd.getpwuid(os.getuid()).pw_dir
    monkeypatch.setenv("HOME", real_home)
    ledger, source = _load_live_ledger()

    assert source == os.path.join(real_home, ".trading", "portfolio", "transactions.json")
    if not ledger:
        pytest.skip("live transaction ledger not present on this host")

    required = {"action", "symbol", "shares", "price", "net_cash_delta"}
    holdings: dict[str, int] = {}
    for i, entry in enumerate(ledger):
        missing = required - set(entry)
        assert not missing, f"ledger entry {i} missing {sorted(missing)}"
        action = str(entry["action"]).upper()
        assert action in {"BUY", "SELL"}, f"entry {i}: unexpected action {action!r}"
        assert int(entry["shares"]) > 0, f"entry {i}: non-positive shares"
        assert float(entry["price"]) > 0, f"entry {i}: non-positive price"
        assert isinstance(entry["net_cash_delta"], (int, float)), f"entry {i}: bad net_cash_delta"

        sym = str(entry["symbol"]).upper()
        signed = int(entry["shares"]) if action == "BUY" else -int(entry["shares"])
        holdings[sym] = holdings.get(sym, 0) + signed
        assert holdings[sym] >= 0, f"FIFO replay oversells {sym} at ledger entry {i}"

    metrics = _compute_live_ledger_metrics(ledger)
    assert metrics["sell_count"] == sum(
        1 for e in ledger if str(e["action"]).upper() == "SELL"
    )
    assert metrics["wins"] + metrics["losses"] == metrics["closed_with_pnl"]
    assert metrics["closed_with_pnl"] <= metrics["sell_count"]
    assert 0.0 <= metrics["win_rate_pct"] <= 100.0
    assert metrics["profit_factor"] is None or metrics["profit_factor"] > 0
