"""Strategy service.

Lists registered strategies, runs a single backtest, and produces
cross-strategy comparison reports.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from .. import config
from ..backtest import fetch_history
from ..backtest.engine import run_backtest
from ..research.comparison import compare_strategies
from ..strategies import REGISTRY, get_strategy, list_strategies


# Status labels per strategy. The benchmark (Strategy A) is frozen.
_STRATEGY_STATUS: dict[str, str] = {
    "A": "Benchmark",
    "C": "Experimental",
}


def list_registered() -> list[dict[str, Any]]:
    """Return one dict per registered strategy.

    Output schema::

        [
          {"key": "A", "name": "SMA(20/50) + RSI(14)", "description": "...",
           "status": "Benchmark", "params": {...}, "version": "1.0"},
          ...
        ]
    """
    out: list[dict[str, Any]] = []
    for key, strat in list_strategies():
        meta = getattr(strat, "meta", None)
        out.append({
            "key": key,
            "name": meta.name if meta else str(strat),
            "description": meta.description if meta else "",
            "status": _STRATEGY_STATUS.get(key, "Research"),
            "params": dict(meta.params) if meta and meta.params else {},
            "version": meta.version if meta else "?",
        })
    return out


def benchmark() -> dict[str, Any]:
    """Return details for the benchmark (Strategy A)."""
    a = REGISTRY.get("A")
    if a is None:
        return {"key": "A", "name": "?", "status": "Benchmark", "description": "Strategy A is the frozen benchmark."}
    meta = a.meta
    return {
        "key": "A",
        "name": meta.name,
        "description": meta.description,
        "status": "Benchmark",
        "params": dict(meta.params),
        "version": meta.version,
        "frozen": True,
    }


def _result_to_dict(result) -> dict[str, Any]:
    """Convert a BacktestResult dataclass to a plain dict."""
    return {
        "pair": result.pair,
        "strategy": result.strategy_name,
        "total_return_pct": round(result.total_return_pct, 2),
        "annualised_return_pct": round(result.annualised_return_pct, 2),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 3),
        "sortino_ratio": round(result.sortino_ratio, 3),
        "win_rate_pct": round(result.win_rate_pct, 2),
        "profit_factor": round(result.profit_factor, 3),
        "volatility_pct": round(result.volatility_pct, 2),
        "calmar_ratio": round(result.calmar_ratio, 3),
        "total_trades": result.total_trades,
        "avg_return_pct": round(result.avg_return_pct, 4),
        "avg_bars_held": round(result.avg_bars_held, 1),
        "buy_and_hold_return_pct": round(result.buy_and_hold_return_pct, 2),
        "data_start": str(result.data_start) if result.data_start else "",
        "data_end": str(result.data_end) if result.data_end else "",
    }


def run(
    strategy: str = "A",
    pair: Optional[str] = None,
    years: float = 2.0,
) -> dict[str, Any]:
    """Run a backtest for one strategy on one pair (or all pairs).

    Output schema is the same as ``_result_to_dict``; when *pair* is
    None the run executes on all configured pairs and a ``per_pair``
    list is included.
    """
    config.ensure_dirs()
    pairs = [pair] if pair else list(config.PAIRS)
    strat = get_strategy(strategy)

    results = []
    for p in pairs:
        df = fetch_history.fetch_history(p, years=years)
        if df.empty:
            results.append({"pair": p, "strategy": strat.name, "error": "no_data"})
            continue
        res = run_backtest(p, df, strategy=strat)
        results.append(_result_to_dict(res))

    if pair:
        return results[0] if results else {"pair": pair, "strategy": strat.name, "error": "no_data"}
    return {"per_pair": results, "strategy": strat.name}


def compare(
    pairs: Optional[list[str]] = None,
    years: float = 2.0,
) -> dict[str, Any]:
    """Run all registered strategies over *pairs* and produce a comparison.

    Output schema::

        {
          "rows": [
            {"strategy": "A", "pair": "SCOM", "return_pct": 5.4,
             "sharpe": 0.32, "drawdown_pct": 6.6, "win_rate_pct": 50.0,
             "verdict": "Baseline", ...},
            ...
          ],
          "regime": "...",
          "data_start": "...", "data_end": "..."
        }
    """
    config.ensure_dirs()
    if pairs is None:
        pairs = list(config.PAIRS)
    strategies_dict = dict(REGISTRY)

    all_rows: list[dict[str, Any]] = []
    last_regime = ""
    data_start = ""
    data_end = ""

    for p in pairs:
        df = fetch_history.fetch_history(p, years=years)
        if df.empty:
            continue
        report = compare_strategies(p, df, strategies_dict, run_backtest)
        for row in report.rows:
            all_rows.append({
                "strategy": row.strategy_name,
                "key": row.key,
                "pair": p,
                "return_pct": round(row.total_return_pct, 2),
                "annualised_return_pct": round(row.annualised_return_pct, 2),
                "sharpe": round(row.sharpe_ratio, 3),
                "drawdown_pct": round(row.max_drawdown_pct, 2),
                "win_rate_pct": round(row.win_rate_pct, 2),
                "profit_factor": round(row.profit_factor, 3),
                "total_trades": row.total_trades,
                "verdict": row.verdict,
            })
        last_regime = report.regime_assessment
        data_start = report.data_start
        data_end = report.data_end

    return {
        "rows": all_rows,
        "regime": last_regime,
        "data_start": data_start,
        "data_end": data_end,
    }
