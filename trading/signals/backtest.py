"""Historical backtest of the signal engine.

Approach
--------
1. Generate the per-bar signal series for ``df``.
2. Pair every BUY with the next SELL into a round-trip trade.
3. If a trade is still open at the end of the series, close it at the
   final bar.
4. Report win rate and average % return per trade.

This is intentionally a *toy* backtest -- it ignores spreads, slippage,
and position sizing. The spec calls out that this is a learning tool.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def run_backtest(df: pd.DataFrame, pair: str) -> dict[str, Any]:
    """Return a backtest summary for ``pair`` given OHLCV ``df``."""
    # Local import keeps this module optional at import time
    from .engine import generate_signals

    signals = generate_signals(df, pair=pair)

    buys = sum(1 for s in signals if s["signal"] == "BUY")
    sells = sum(1 for s in signals if s["signal"] == "SELL")

    trades: list[dict[str, float]] = []
    open_trade: dict[str, float] | None = None

    for s in signals:
        verdict = s["signal"]
        price = float(s["price"])
        if verdict == "BUY" and open_trade is None:
            open_trade = {"entry": price, "entry_date": s["date"]}
        elif verdict == "SELL" and open_trade is not None:
            entry = open_trade["entry"]
            ret_pct = (price - entry) / entry * 100.0
            trades.append(
                {
                    "entry_date": open_trade["entry_date"],
                    "exit_date": s["date"],
                    "entry": entry,
                    "exit": price,
                    "return_pct": ret_pct,
                    "win": ret_pct > 0.0,
                }
            )
            open_trade = None

    # Force-close any still-open trade at the last bar
    if open_trade is not None and signals:
        last = signals[-1]
        price = float(last["price"])
        entry = open_trade["entry"]
        ret_pct = (price - entry) / entry * 100.0
        trades.append(
            {
                "entry_date": open_trade["entry_date"],
                "exit_date": last["date"],
                "entry": entry,
                "exit": price,
                "return_pct": ret_pct,
                "win": ret_pct > 0.0,
            }
        )

    wins = sum(1 for t in trades if t["win"])
    losses = len(trades) - wins
    win_rate = (wins / len(trades) * 100.0) if trades else 0.0
    avg_ret = sum(t["return_pct"] for t in trades) / len(trades) if trades else 0.0

    return {
        "pair": pair,
        "n_signals": len(signals),
        "buys": buys,
        "sells": sells,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_return_pct": avg_ret,
    }
