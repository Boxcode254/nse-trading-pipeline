"""Strategy comparison engine.

Runs multiple strategies over the same pairs and produces a
side-by-side comparison table.

Example output::

    Strategy         | Return  | Sharpe  | DD    | Win Rt | Verdict
    ─────────────────┼─────────┼─────────┼───────┼────────┼────────
    SMA(20/50)+RSI14 | +5.4%   | 0.32    | 6.6%  | 50%    | Baseline
    SMA+200 filter   | +3.1%   | 0.25    | 4.2%  | 43%    | Worse
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from .risk_metrics import compute_expanded_metrics
from .regimes import compute_regime_breakdown


@dataclass
class ComparisonRow:
    """One row in the strategy comparison table."""
    key: str
    strategy_name: str
    total_return_pct: float = 0.0
    annualised_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    volatility_pct: float = 0.0
    calmar_ratio: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_return_pct: float = 0.0
    time_in_market_pct: float = 0.0
    trade_frequency: float = 0.0
    verdict: str = ""


@dataclass
class ComparisonReport:
    """Full comparison report for one pair across multiple strategies."""
    pair: str
    rows: list[ComparisonRow] = field(default_factory=list)
    data_start: str = ""
    data_end: str = ""
    regime_assessment: str = ""


def compare_strategies(
    pair: str,
    df: pd.DataFrame,
    strategies: dict[str, Any],
    backtest_fn,
) -> ComparisonReport:
    """Run *strategies* over *df* for *pair* and produce a comparison.

    Parameters
    ----------
    pair : str
        Human-readable pair name.
    df : pd.DataFrame
        OHLCV DataFrame with columns ``open, high, low, close, volume``.
    strategies : dict[str, BaseStrategy]
        Dict of ``key → strategy instance`` to compare.
    backtest_fn : callable
        The backtest engine function that takes ``(pair, df, strategy)``
        and returns a ``BacktestResult``.

    Returns
    -------
    ComparisonReport
        One row per strategy, all sharing the same ``pair``.
    """
    report = ComparisonReport(pair=pair)
    report.data_start = str(df.index[0].date()) if not df.empty else ""
    report.data_end = str(df.index[-1].date()) if not df.empty else ""

    if df.empty:
        return report

    for key, strategy in strategies.items():
        # Run backtest
        result = backtest_fn(pair, df, strategy)

        if result.total_trades == 0:
            report.rows.append(
                ComparisonRow(key=key, strategy_name=strategy.name, verdict="No trades")
            )
            continue

        # Expanded metrics
        from ..backtest.engine import BacktestResult
        # We already have a BacktestResult; compute extra metrics
        daily_signals = None
        try:
            signals = strategy.generate_signals(strategy.prepare(df.copy()))
            daily_signals = signals
        except Exception:
            pass

        extra = compute_expanded_metrics(
            result.equity_curve, result.trades, df["close"],
            warmup=max(50, 14 + 5),
        )

        # Regime analysis
        regime = compute_regime_breakdown(
            pair, df["close"], result.trades, result.equity_curve, daily_signals
        )

        # Build the row
        row = ComparisonRow(
            key=key,
            strategy_name=strategy.name,
            total_return_pct=extra.get("total_return_pct", 0.0),
            annualised_return_pct=extra.get("annualised_return_pct", 0.0),
            sharpe_ratio=extra.get("sharpe_ratio", 0.0),
            sortino_ratio=extra.get("sortino_ratio", 0.0),
            max_drawdown_pct=extra.get("max_drawdown_pct", 0.0),
            volatility_pct=extra.get("volatility_pct", 0.0),
            calmar_ratio=extra.get("calmar_ratio", 0.0),
            win_rate_pct=extra.get("win_rate_pct", 0.0),
            profit_factor=extra.get("profit_factor", 0.0),
            total_trades=extra.get("total_trades", 0),
            avg_return_pct=extra.get("avg_return_pct", 0.0),
            time_in_market_pct=extra.get("time_in_market_pct", 0.0),
            trade_frequency=extra.get("trade_frequency_per_year", 0.0),
            verdict=_assign_verdict(row_template := None, result, extra, regime),
        )

        # Carry regime assessment
        report.regime_assessment = regime.overall_assessment

        report.rows.append(row)

    return report


def _assign_verdict(
    _row_template,
    result,
    extra: dict,
    regime,
) -> str:
    """Return a human-readable verdict for a strategy."""
    parts = []

    # Return vs buy-and-hold
    ret = extra.get("total_return_pct", 0)
    bh = result.buy_and_hold_return_pct
    if ret > bh * 0.9:
        parts.append("Matches BH")
    elif ret < bh * 0.3 and bh > 0:
        parts.append(f"Underperforms BH")
    elif ret < 0:
        parts.append("Loss-making")

    # Risk-adjusted
    sharpe = extra.get("sharpe_ratio", 0)
    if sharpe > 1.0:
        parts.append("Strong risk-adjusted")
    elif sharpe > 0.5:
        parts.append("Good risk-adjusted")
    elif sharpe < 0:
        parts.append("Negative risk-adjusted")

    # Sortino
    sortino = extra.get("sortino_ratio", 0)
    if sortino > sharpe * 1.5 and sharpe > 0:
        parts.append("Downside protection")

    # Drawdown
    dd = extra.get("max_drawdown_pct", 100)
    if dd < 10:
        parts.append("Low drawdown")
    elif dd > 30:
        parts.append("High drawdown")

    # Regime insight
    if "bear" in regime.overall_assessment.lower():
        parts.append("Bear-market hedge" if ret > 0 else "Still loses in bears")

    return " | ".join(parts) if parts else "Neutral"


def format_comparison_table(report: ComparisonReport) -> str:
    """Format a ``ComparisonReport`` as a text table."""
    if not report.rows:
        return f"_{report.pair}_ — No comparison data"

    lines = [f"**{report.pair}**"]
    if report.regime_assessment:
        lines.append(f"_{report.regime_assessment}_")
    lines.append("")

    # Header
    header = f"{'Strategy':<28s} {'Return':>8s} {'Ann.':>7s} {'Sharpe':>7s} {'Sortino':>7s}  {'DD':>6s}  {'W/R':>5s}  {'Trades':>6s}  {'Verdict'}"
    sep = "─" * len(header)
    lines.append(header)
    lines.append(sep)

    for row in report.rows:
        ret_s = f"{row.total_return_pct:+.1f}%"
        ann_s = f"{row.annualised_return_pct:+.1f}%" if row.annualised_return_pct else "—"
        sharpe_s = f"{row.sharpe_ratio:.2f}" if row.sharpe_ratio else "—"
        sortino_s = f"{row.sortino_ratio:.2f}" if row.sortino_ratio else "—"
        dd_s = f"{row.max_drawdown_pct:.1f}%" if row.max_drawdown_pct else "—"
        wr_s = f"{row.win_rate_pct:.0f}%" if row.win_rate_pct else "—"
        td_s = str(row.total_trades) if row.total_trades else "—"
        lines.append(
            f"{row.strategy_name:<28s} {ret_s:>8s} {ann_s:>7s} {sharpe_s:>7s} "
            f"{sortino_s:>7s}  {dd_s:>6s}  {wr_s:>5s}  {td_s:>6s}  {row.verdict}"
        )

    return "\n".join(lines)


def format_multi_pair_comparison(reports: list[ComparisonReport]) -> str:
    """Format a summary table across pairs + per-pair detail.

    This is the main output of ``python3 -m trading compare``.
    """
    if not reports:
        return "No comparison data."

    lines = [
        "📊 *STRATEGY COMPARISON REPORT*",
        "Multi-pair, multi-strategy evaluation — SMA(20/50)+RSI(14) benchmark frozen.",
        "",
    ]

    # ── Summary cross-pair table ──────────────────────────────────
    # Collect all strategy keys
    all_keys = set()
    for r in reports:
        for row in r.rows:
            all_keys.add(row.key)
    all_keys = sorted(all_keys)

    # For each key, average across pairs
    key_avgs: dict[str, dict] = {k: {"ret": [], "sharpe": [], "dd": [], "wr": []} for k in all_keys}
    for r in reports:
        for row in r.rows:
            k = row.key
            key_avgs[k]["ret"].append(row.total_return_pct)
            key_avgs[k]["sharpe"].append(row.sharpe_ratio)
            key_avgs[k]["dd"].append(row.max_drawdown_pct)
            key_avgs[k]["wr"].append(row.win_rate_pct)

    lines.append("**Cross-Pair Averages:**")
    header = f"{'Strategy':<28s} {'Ø Return':>8s} {'Ø Sharpe':>8s} {'Ø DD':>6s} {'Ø Win Rt':>7s} {'Pairs':>5s}"
    lines.append(header)
    lines.append("─" * len(header))

    for k in all_keys:
        avg = key_avgs[k]
        n = len(avg["ret"])
        if n == 0:
            continue
        # Get strategy name from first report
        sname = ""
        for r in reports:
            for row in r.rows:
                if row.key == k:
                    sname = row.strategy_name
                    break
            if sname:
                break
        ret_avg = sum(avg["ret"]) / n
        sharpe_avg = sum(avg["sharpe"]) / n if any(s for s in avg["sharpe"]) else 0
        dd_avg = sum(avg["dd"]) / n if any(d for d in avg["dd"]) else 0
        wr_avg = sum(avg["wr"]) / n if any(w for w in avg["wr"]) else 0
        lines.append(
            f"{sname:<28s} {ret_avg:>+7.1f}% {sharpe_avg:>8.2f} "
            f"{dd_avg:>5.1f}% {wr_avg:>6.1f}% {n:>4d}"
        )

    lines.append("")
    lines.append("─" * 60)
    lines.append("")

    # ── Per-pair detail ───────────────────────────────────────────
    for r in reports:
        lines.append(format_comparison_table(r))
        lines.append("")

    # ── Key findings ──────────────────────────────────────────────
    lines.append("─" * 60)
    lines.append("*Key Findings:*")
    for r in reports:
        if r.regime_assessment:
            lines.append(f"• {r.pair}: {r.regime_assessment}")

    return "\n".join(lines)
