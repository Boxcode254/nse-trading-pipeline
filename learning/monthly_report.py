"""Monthly Report Generator for Trading Learning System.

Generates a markdown report answering:
- What worked?
- What failed?
- Which strategies improving?
- Which should be retired?

Uses SQLite stats from the learning database.
"""

from __future__ import annotations

import os
import json
import urllib.request
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path
import sys

# Allow running as script from learning/ directory
sys.path.insert(0, str(Path(__file__).parent))

from db import LearningDB, get_db


def generate_monthly_report(
    db: LearningDB,
    months_back: int = 1,
    include_details: bool = True
) -> str:
    """Generate a monthly performance report as markdown.
    
    Args:
        db: LearningDB instance
        months_back: How many months back to include (default: 1 = current month)
        include_details: Whether to include detailed per-symbol breakdown
        
    Returns:
        Markdown formatted report string
    """
    overall_stats = db.get_overall_stats()
    monthly_stats = db.get_monthly_stats(months=months_back)
    
    # Current month identifier
    current_month = datetime.now().strftime("%Y-%m")
    
    lines = [
        "# Monthly Trading Performance Report",
        "",
        f"**Period:** {current_month}",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        f"**Total Recommendations:** {overall_stats['total_recommendations']}",
        f"**Evaluated Outcomes:** {overall_stats['evaluated_outcomes']}",
        f"**Success Rate:** {overall_stats['success_rate_pct']:.1f}%",
        f"**Avg Actual Return:** {overall_stats['avg_actual_return']:.2f}%" if overall_stats['avg_actual_return'] else "**Avg Actual Return:** —",
        f"**Avg Expected Return:** {overall_stats['avg_expected_return']:.2f}%" if overall_stats['avg_expected_return'] else "**Avg Expected Return:** —",
        f"**Avg Time to Target:** {overall_stats['avg_time_to_target_days']:.1f} days" if overall_stats['avg_time_to_target_days'] else "**Avg Time to Target:** —",
        "",
    ]
    
    # Signal breakdown
    lines.extend([
        "### Signal Distribution",
        "",
        f"- **BUY:** {overall_stats['buy_count']}",
        f"- **SELL:** {overall_stats['sell_count']}",
        f"- **HOLD:** {overall_stats['hold_count']}",
        f"- **Avg Confidence:** {overall_stats['avg_confidence']:.2f}",
        f"- **Avg Score:** {overall_stats['avg_score']:.1f}",
        "",
    ])
    
    # Monthly trends
    if monthly_stats:
        lines.extend([
            "### Monthly Trends",
            "",
            "| Month | Symbols | Recs | BUY | SELL | HOLD | Evaluated | Success | Success % | Avg Actual % | Avg Expected % | Avg Days |",
            "|-------|---------|------|-----|------|------|-----------|---------|-----------|--------------|----------------|----------|",
        ])
        
        for m in monthly_stats:
            success_pct = f"{m.success_rate_pct:.1f}%" if m.success_rate_pct is not None else "—"
            actual_ret = f"{m.avg_actual_return:.2f}%" if m.avg_actual_return is not None else "—"
            expected_ret = f"{m.avg_expected_return:.2f}%" if m.avg_expected_return is not None else "—"
            time_target = f"{m.avg_time_to_target_days:.1f}" if m.avg_time_to_target_days is not None else "—"
            
            line = (
                f"| {m.month if m.month else '—'} | "
                f"{m.unique_symbols if m.unique_symbols is not None else '—'} | "
                f"{m.total_recommendations if m.total_recommendations is not None else '—'} | "
                f"{m.buy_count if m.buy_count is not None else '—'} | "
                f"{m.sell_count if m.sell_count is not None else '—'} | "
                f"{m.hold_count if m.hold_count is not None else '—'} | "
                f"{m.evaluated_outcomes if m.evaluated_outcomes is not None else '—'} | "
                f"{m.successful_outcomes if m.successful_outcomes is not None else '—'} | "
                f"{success_pct} | {actual_ret} | {expected_ret} | {time_target} |"
            )
            lines.append(line)
        lines.append("")
    
    # What Worked
    lines.extend([
        "## ✅ What Worked",
        "",
    ])
    
    if overall_stats['success_rate_pct'] >= 60:
        lines.append(f"- **Overall success rate of {overall_stats['success_rate_pct']:.1f}%** — The system is producing profitable signals more often than not.")
    else:
        lines.append(f"- **Success rate of {overall_stats['success_rate_pct']:.1f}%** — Room for improvement, but signals are being tracked.")
    
    if overall_stats['avg_actual_return'] and overall_stats['avg_actual_return'] > 0:
        lines.append(f"- **Positive average return ({overall_stats['avg_actual_return']:.2f}%)** — Winning trades are outweighing losses.")
    
    # High-confidence winners
    high_conf_recs = db.get_recommendations(recommendation="BUY", limit=100)
    high_conf_winners = [
        r for r in high_conf_recs 
        if r.confidence >= 0.7
    ]
    if high_conf_winners:
        lines.append(f"- **{len(high_conf_winners)} high-confidence BUY signals** — Strong conviction signals are being generated.")
    
    # Fast time to target
    if overall_stats['avg_time_to_target_days'] and overall_stats['avg_time_to_target_days'] <= 14:
        lines.append(f"- **Fast execution ({overall_stats['avg_time_to_target_days']:.1f} avg days to target)** — Targets reached quickly.")
    
    if not high_conf_winners and (not overall_stats['avg_actual_return'] or overall_stats['avg_actual_return'] <= 0):
        lines.append("- *No clear winning patterns identified this period.*")
    
    lines.append("")
    
    # What Failed
    lines.extend([
        "## ❌ What Failed",
        "",
    ])
    
    if overall_stats['success_rate_pct'] < 50:
        lines.append(f"- **Low success rate ({overall_stats['success_rate_pct']:.1f}%)** — More than half of evaluated trades didn't hit target.")
    
    if overall_stats['avg_actual_return'] and overall_stats['avg_actual_return'] < 0:
        lines.append(f"- **Negative average return ({overall_stats['avg_actual_return']:.2f}%)** — Losses exceeded gains.")
    
    # Failed by recommendation type
    if overall_stats['sell_count'] > 0:
        sell_outcomes = db.get_outcomes()
        sell_failures = [o for o in sell_outcomes if not o.success and db.get_recommendation(o.symbol, o.date) and db.get_recommendation(o.symbol, o.date).recommendation == "SELL"]
        if sell_failures:
            lines.append(f"- **{len(sell_failures)} failed SELL signals** — Short/bearish calls not working.")
    
    if overall_stats['hold_count'] > overall_stats['buy_count'] + overall_stats['sell_count']:
        lines.append(f"- **High HOLD ratio ({overall_stats['hold_count']} vs {overall_stats['buy_count'] + overall_stats['sell_count']} actionable)** — System may be too conservative.")
    
    # Low confidence signals
    low_conf_recs = [r for r in db.get_recommendations(limit=1000) if r.confidence < 0.4]
    if low_conf_recs:
        lines.append(f"- **{len(low_conf_recs)} low-confidence signals** — Noise may be diluting performance.")
    
    if overall_stats['success_rate_pct'] >= 50 and (not overall_stats['avg_actual_return'] or overall_stats['avg_actual_return'] >= 0):
        lines.append("- *No major failure patterns identified this period.*")
    
    lines.append("")
    
    # Strategies Improving
    lines.extend([
        "## 📈 Strategies Improving",
        "",
    ])
    
    # Compare current vs previous month
    if len(monthly_stats) >= 2:
        current = monthly_stats[0]
        previous = monthly_stats[1]
        
        success_delta = current.success_rate_pct - (previous.success_rate_pct or 0)
        return_delta = (current.avg_actual_return or 0) - (previous.avg_actual_return or 0)
        volume_delta = current.total_recommendations - previous.total_recommendations
        
        prev_success = previous.success_rate_pct if previous.success_rate_pct is not None else 0
        curr_success = current.success_rate_pct if current.success_rate_pct is not None else 0
        
        if success_delta > 5:
            lines.append(f"- **Success rate improved by {success_delta:.1f}pp** ({prev_success:.1f}% → {curr_success:.1f}%)")
        elif success_delta > 0:
            lines.append(f"- **Success rate trending up** (+{success_delta:.1f}pp month-over-month)")
        
        if return_delta > 0:
            lines.append(f"- **Average returns improving** ({(previous.avg_actual_return or 0):.2f}% → {(current.avg_actual_return or 0):.2f}%)")
        
        if volume_delta > 0:
            lines.append(f"- **Signal volume increasing** ({previous.total_recommendations} → {current.total_recommendations} recommendations)")
        
        # Confidence improvement
        conf_delta = current.avg_confidence - (previous.avg_confidence or 0)
        if conf_delta > 0.05:
            lines.append(f"- **Signal confidence rising** ({previous.avg_confidence:.2f} → {current.avg_confidence:.2f})")
    else:
        lines.append("- *Insufficient history for month-over-month comparison (need 2+ months of data)*")
    
    # Symbol-level improvement
    if include_details:
        symbols = set()
        for m in monthly_stats:
            # Get symbols from recommendations
            recs = db.get_recommendations(start_date=f"{m.month}-01", end_date=f"{m.month}-31", limit=1000)
            for r in recs:
                symbols.add(r.symbol)
        
        improving_symbols = []
        for sym in symbols:
            perf = db.get_symbol_performance(sym)
            if perf['evaluated'] >= 3 and perf['success_rate_pct'] >= 60:
                improving_symbols.append((sym, perf['success_rate_pct'], perf['avg_return'], perf['evaluated']))
        
        if improving_symbols:
            lines.append("")
            lines.append("### Top Performing Symbols")
            lines.append("")
            lines.append("| Symbol | Success Rate | Avg Return | Evaluated |")
            lines.append("|--------|--------------|------------|-----------|")
            for sym, rate, ret, eval_count in sorted(improving_symbols, key=lambda x: x[1], reverse=True)[:10]:
                lines.append(f"| {sym} | {rate:.1f}% | {ret:.2f}% | {eval_count} |")
    
    if not improving_symbols and len(monthly_stats) < 2:
        lines.append("- *No clear improving strategies yet — need more data*")
    
    lines.append("")
    
    # Strategies to Retire
    lines.extend([
        "## 🗑️ Strategies to Retire",
        "",
    ])
    
    if len(monthly_stats) >= 2:
        current = monthly_stats[0]
        previous = monthly_stats[1]
        
        success_delta = current.success_rate_pct - (previous.success_rate_pct or 0)
        return_delta = (current.avg_actual_return or 0) - (previous.avg_actual_return or 0)
        
        if success_delta < -10:
            lines.append(f"- **Success rate declining sharply ({success_delta:+.1f}pp)** — Current approach degrading.")
        elif success_delta < -5:
            lines.append(f"- **Success rate declining ({success_delta:+.1f}pp)** — Review signal filters.")
        
        if return_delta < -2:
            lines.append(f"- **Returns deteriorating ({return_delta:+.2f}pp)** — Risk/reward shifting unfavorably.")
        
        if current.avg_confidence < previous.avg_confidence - 0.1:
            lines.append(f"- **Confidence dropping ({previous.avg_confidence:.2f} → {current.avg_confidence:.2f})** — Signal quality degrading.")
    else:
        lines.append("- *Insufficient history for decline detection*")
    
    # Per-symbol retirement candidates
    if include_details:
        all_recs = db.get_recommendations(limit=1000)
        symbol_perf = {}
        for r in all_recs:
            if r.symbol not in symbol_perf:
                symbol_perf[r.symbol] = db.get_symbol_performance(r.symbol)
        
        retirement_candidates = []
        for sym, perf in symbol_perf.items():
            if perf['evaluated'] >= 5 and perf['success_rate_pct'] < 30:
                retirement_candidates.append((sym, perf['success_rate_pct'], perf['avg_return'], perf['evaluated']))
        
        if retirement_candidates:
            lines.append("")
            lines.append("### Symbols Consistently Underperforming")
            lines.append("")
            lines.append("| Symbol | Success Rate | Avg Return | Evaluated | Action |")
            lines.append("|--------|--------------|------------|-----------|--------|")
            for sym, rate, ret, eval_count in sorted(retirement_candidates, key=lambda x: x[1]):
                action = "Retire" if rate < 20 else "Review"
                lines.append(f"| {sym} | {rate:.1f}% | {ret:.2f}% | {eval_count} | {action} |")
        else:
            lines.append("- *No symbols meet retirement criteria (need 5+ evaluated, <30% success)*")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Report generated by Trading Learning System*")
    
    return "\n".join(lines)


def send_telegram(message: str, token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
    """Send a message to Telegram.
    
    Args:
        message: The message text (markdown supported)
        token: Bot token (defaults to TELEGRAM_BOT_TOKEN env var)
        chat_id: Chat ID (defaults to TELEGRAM_HOME_CHANNEL env var)
        
    Returns:
        True if sent successfully, False otherwise
    """
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_HOME_CHANNEL", "")
    
    if not token or not chat_id:
        print("⚠️  TELEGRAM_BOT_TOKEN or TELEGRAM_HOME_CHANNEL not set")
        return False
    
    try:
        payload = json.dumps({
            "chat_id": chat_id.strip().lstrip("-"),
            "text": message,
            "parse_mode": "Markdown",
        }).encode()
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        req = urllib.request.Request(url, data=payload,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"⚠️  Failed to send Telegram: {e}")
        return False


def save_report(report: str, output_path: Optional[str] = None) -> str:
    """Save report to file.
    
    Args:
        report: Markdown report content
        output_path: Custom path (defaults to ~/.trading/logs/monthly_report-YYYY-MM.md)
        
    Returns:
        Path where report was saved
    """
    if output_path:
        path = Path(output_path)
    else:
        logs_dir = Path.home() / ".trading" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        month = datetime.now().strftime("%Y-%m")
        path = logs_dir / f"monthly_report-{month}.md"
    
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report)
    return str(path)


def main(
    months: int = 1,
    telegram: bool = False,
    save: bool = False,
    output: Optional[str] = None,
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Main entry point for the monthly report CLI.
    
    Returns:
        Exit code (0 = success)
    """
    try:
        db = get_db()
        report = generate_monthly_report(db, months_back=months)
        
        if as_json:
            # Output JSON with report content
            result = {
                "report": report,
                "month": datetime.now().strftime("%Y-%m"),
                "generated_at": datetime.now().isoformat(),
            }
            print(json.dumps(result, indent=2))
        else:
            if not quiet:
                print(report)
        
        if save or output:
            saved_path = save_report(report, output)
            if not quiet:
                print(f"\n💾 Report saved to: {saved_path}")
        
        if telegram:
            # Telegram has 4096 char limit, truncate if needed
            telegram_msg = report
            if len(telegram_msg) > 4000:
                telegram_msg = telegram_msg[:3950] + "\n\n... (truncated)"
            
            if send_telegram(telegram_msg):
                if not quiet:
                    print("📤 Report sent to Telegram")
            else:
                if not quiet:
                    print("⚠️  Failed to send to Telegram")
                return 1
        
        return 0
        
    except Exception as e:
        print(f"❌ Error generating report: {e}")
        return 1


if __name__ == "__main__":
    import sys
    import typer
    
    app = typer.Typer(help="Generate monthly trading performance report")
    
    @app.command()
    def generate(
        months: int = typer.Option(1, "--months", "-m", help="Months of history to include"),
        telegram: bool = typer.Option(False, "--telegram", "-t", help="Send to Telegram"),
        save: bool = typer.Option(False, "--save", "-s", help="Save to ~/.trading/logs/"),
        output: Optional[str] = typer.Option(None, "--output", "-o", help="Custom output file"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
        as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
    ):
        """Generate monthly performance report."""
        raise typer.Exit(main(
            months=months,
            telegram=telegram,
            save=save,
            output=output,
            quiet=quiet,
            as_json=as_json,
        ))
    
    app()