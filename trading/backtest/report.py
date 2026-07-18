"""Formatted backtest & research reports for Telegram/console.

Produces compact, human-readable summaries suitable for Telegram's
4096-char limit.
"""
from __future__ import annotations

from typing import Optional

from .engine import BacktestResult
from ..research.regimes import compute_regime_breakdown
from ..research.risk_metrics import compute_expanded_metrics


def format_backtest_results(
    results: list[BacktestResult],
    regime_analysis: bool = True,
) -> str:
    """Return a formatted multi-pair backtest report string.

    Parameters
    ----------
    results : list[BacktestResult]
        One per pair, in display order.
    regime_analysis : bool
        Include market regime breakdown per pair.

    Returns
    -------
    str
        Formatted report.
    """
    if not results:
        return "⚠️ No backtest results to display."

    strategy_name = results[0].strategy_name if results else "Unknown"
    lines: list[str] = []
    lines.append(f"📊 *BACKTEST RESULTS — {strategy_name}*")
    lines.append("_" * 40)

    # ── Overall summary ───────────────────────────────────────────
    total_ret = sum(r.total_return_pct for r in results if r.total_trades > 0)
    avg_win_rate = (
        sum(r.win_rate_pct for r in results if r.total_trades > 0)
        / max(sum(1 for r in results if r.total_trades > 0), 1)
    )
    n_active = sum(1 for r in results if r.total_trades > 0)

    lines.append(f"• Pairs tested: {len(results)}  |  With signals: {n_active}")
    lines.append(f"• Avg win rate: {avg_win_rate:.1f}%")
    lines.append(f"• Sum of returns: {total_ret:+.1f}%")
    lines.append("")

    # ── Per-pair cards ────────────────────────────────────────────
    for r in results:
        if r.total_trades == 0:
            lines.append(f"_{r.pair}_ — ⏳ Insufficient data")
            continue

        grade = _grade(r)
        lines.append(f"**{r.pair}** {grade}")
        lines.append(
            f"  Return: {r.total_return_pct:+.1f}%  "
            f"| BH: {r.buy_and_hold_return_pct:+.1f}%"
        )
        lines.append(
            f"  Trades: {r.total_trades}  "
            f"| Win rate: {r.win_rate_pct:.0f}%  "
            f"| Avg: {r.avg_return_pct:+.1f}%"
        )
        lines.append(
            f"  Win Ø {r.avg_win_pct:+.1f}%  "
            f"| Loss Ø {r.avg_loss_pct:.1f}%  "
            f"| PF: {r.profit_factor:.2f}"
        )
        # Risk-adjusted line
        lines.append(
            f"  Sharpe: {r.sharpe_ratio:.2f}  "
            f"| Sortino: {r.sortino_ratio:.2f}  "
            f"| Calmar: {r.calmar_ratio:.2f}"
        )
        lines.append(
            f"  Vol: {r.volatility_pct:.1f}%  "
            f"| Max DD: {r.max_drawdown_pct:.1f}%  "
            f"| In market: {r.time_in_market_pct:.0f}%"
        )
        lines.append(
            f"  Trades/yr: {r.trade_frequency_per_year:.1f}  "
            f"| Avg hold: {r.avg_holding_days:.0f}d"
        )
        lines.append(f"  Period: {r.data_start} → {r.data_end}")

        # ── Regime breakdown ──────────────────────────────────────
        if regime_analysis and r.regime_breakdown:
            rb = r.regime_breakdown
            if rb.get("overall_assessment"):
                lines.append(f"  🔍 {rb['overall_assessment']}")
            # Per-regime returns
            reg_parts = []
            for reg in ["Bull", "Bear", "Sideways"]:
                ret = rb.get("total_return_pct", {}).get(reg, None)
                if ret is not None:
                    reg_parts.append(f"{reg}: {ret:+.1f}%")
            if reg_parts:
                lines.append(f"  Regime returns: {' | '.join(reg_parts)}")
        lines.append("")

    # ── Legend ────────────────────────────────────────────────────
    lines.append("_" * 40)
    lines.append("BH = Buy & Hold  |  PF = Profit Factor  |  DD = Drawdown")
    lines.append("Calmar = Ann. Return / Max DD  |  Sortino = downside Sharpe")
    lines.append("⭐ = Excellent  ✅ = Good  ⚠️= Weak  ❌ = Poor")
    return "\n".join(lines)


def _grade(r: BacktestResult) -> str:
    """Return an emoji grade for a backtest result."""
    score = 0
    if r.sharpe_ratio > 1.0:
        score += 2
    elif r.sharpe_ratio > 0.5:
        score += 1
    if r.sortino_ratio > r.sharpe_ratio * 1.2 and r.sharpe_ratio > 0:
        score += 1  # downside protection
    if r.win_rate_pct > 55:
        score += 1
    if r.total_return_pct > r.buy_and_hold_return_pct:
        score += 1
    if r.max_drawdown_pct < 15:
        score += 1
    if r.profit_factor > 2.0:
        score += 1
    if r.calmar_ratio > 0.5:
        score += 1
    if score >= 6:
        return "⭐"
    if score >= 4:
        return "✅"
    if score >= 2:
        return "⚠️"
    return "❌"
