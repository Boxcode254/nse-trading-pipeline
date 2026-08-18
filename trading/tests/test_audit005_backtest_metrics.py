"""Regression tests for AUDIT-005 backtest win metrics."""

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


def test_live_ledger_loader_reads_canonical_portfolio_file(monkeypatch):
    # Other test modules poison HOME at import time. Restore the account home
    # for this integration check so Path.home() resolves the canonical ledger.
    import os
    import pwd

    real_home = pwd.getpwuid(os.getuid()).pw_dir
    monkeypatch.setenv("HOME", real_home)
    ledger, source = _load_live_ledger()

    assert source == os.path.join(real_home, ".trading", "portfolio", "transactions.json")
    assert len(ledger) == 82
