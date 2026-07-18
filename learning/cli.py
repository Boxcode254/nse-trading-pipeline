"""CLI for the Trading Learning Database."""

import typer
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from typing import Optional, List
from datetime import date, datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from db import LearningDB, Recommendation, Outcome, get_db
from outcomes import (
    OutcomeRecorder,
    RecommendationOutcome,
    DailyPriceClose,
    get_recorder,
    record_daily_closes_from_market_service,
)

app = typer.Typer(help="Trading Learning Database CLI")
console = Console()


@app.command()
def init():
    """Initialize the learning database."""
    db = get_db()
    console.print("[green]✓[/green] Learning database initialized at ~/.trading/learning/learning.db")


@app.command()
def add_rec(
    symbol: str,
    rec_date: str = typer.Argument(..., help="Date (YYYY-MM-DD)"),
    confidence: float = typer.Option(..., help="Confidence 0.0-1.0"),
    recommendation: str = typer.Option(..., help="BUY, SELL, or HOLD"),
    score: float = typer.Option(..., help="Score 0-100"),
    factors: str = typer.Option("{}", help="JSON string of factors"),
):
    """Add a recommendation."""
    db = get_db()
    try:
        factors_dict = eval(factors)  # Simple eval for JSON-like input
    except Exception:
        import json
        factors_dict = json.loads(factors)

    rec = Recommendation(
        symbol=symbol.upper(),
        date=rec_date,
        confidence=confidence,
        recommendation=recommendation.upper(),
        score=score,
        factors=factors_dict
    )
    rec_id = db.add_recommendation(rec)
    console.print(f"[green]✓[/green] Added recommendation #{rec_id}: {symbol} {recommendation} @ {rec_date}")


@app.command()
def list_recs(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s"),
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
    rec_type: Optional[str] = typer.Option(None, "--type", "-t"),
    limit: int = typer.Option(20, "--limit", "-l"),
):
    """List recommendations."""
    db = get_db()
    recs = db.get_recommendations(
        symbol=symbol.upper() if symbol else None,
        start_date=start,
        end_date=end,
        recommendation=rec_type.upper() if rec_type else None,
        limit=limit
    )

    table = Table(title="Recommendations")
    table.add_column("ID", style="dim")
    table.add_column("Symbol")
    table.add_column("Date")
    table.add_column("Rec")
    table.add_column("Confidence")
    table.add_column("Score")
    table.add_column("Timestamp")

    for r in recs:
        table.add_row(
            str(r.id), r.symbol, r.date, r.recommendation,
            f"{r.confidence:.2f}", f"{r.score:.1f}", r.timestamp[:19]
        )
    console.print(table)


@app.command()
def add_outcome(
    symbol: str,
    rec_date: str = typer.Argument(..., help="Recommendation date (YYYY-MM-DD)"),
    market_outcome: str = typer.Option(..., help="UP, DOWN, or FLAT"),
    expected: Optional[float] = typer.Option(None, "--expected", "-e", help="Expected return %"),
    actual: Optional[float] = typer.Option(None, "--actual", "-a", help="Actual return %"),
    time_to_target: Optional[int] = typer.Option(None, "--days", "-d", help="Days to target"),
    success: Optional[bool] = typer.Option(None, "--success/--fail", help="Outcome success"),
):
    """Add or update an outcome."""
    db = get_db()

    # Auto-compute success if not provided
    if success is None:
        # Get the recommendation to determine expected direction
        rec = db.get_recommendation(symbol.upper(), rec_date)
        if rec:
            if rec.recommendation == "BUY":
                success = market_outcome == "UP"
            elif rec.recommendation == "SELL":
                success = market_outcome == "DOWN"
            else:
                success = market_outcome == "FLAT"
        else:
            success = False

    outcome = Outcome(
        symbol=symbol.upper(),
        date=rec_date,
        market_outcome=market_outcome.upper(),
        expected_return=expected,
        actual_return=actual,
        time_to_target=time_to_target,
        success=success
    )
    outcome_id = db.add_outcome(outcome)
    console.print(f"[green]✓[/green] Added outcome #{outcome_id}: {symbol} {market_outcome} (success={success})")


@app.command()
def list_outcomes(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s"),
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
    limit: int = typer.Option(20, "--limit", "-l"),
):
    """List outcomes."""
    db = get_db()
    outcomes = db.get_outcomes(
        symbol=symbol.upper() if symbol else None,
        start_date=start,
        end_date=end,
        limit=limit
    )

    table = Table(title="Outcomes")
    table.add_column("ID", style="dim")
    table.add_column("Symbol")
    table.add_column("Date")
    table.add_column("Market")
    table.add_column("Expected %")
    table.add_column("Actual %")
    table.add_column("Days")
    table.add_column("Success")

    for o in outcomes:
        table.add_row(
            str(o.id), o.symbol, o.date, o.market_outcome,
            f"{o.expected_return:.2f}" if o.expected_return else "—",
            f"{o.actual_return:.2f}" if o.actual_return else "—",
            str(o.time_to_target) if o.time_to_target else "—",
            "✓" if o.success else "✗"
        )
    console.print(table)


@app.command()
def stats(
    months: int = typer.Option(12, "--months", "-m"),
    overall: bool = typer.Option(False, "--overall", "-o"),
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s"),
):
    """Show performance statistics."""
    db = get_db()

    if overall:
        stats = db.get_overall_stats()
        panel = Panel.fit(
            f"[bold]Total Recommendations:[/bold] {stats['total_recommendations']}\n"
            f"[bold]Buys:[/bold] {stats['buy_count']}  "
            f"[bold]Sells:[/bold] {stats['sell_count']}  "
            f"[bold]Holds:[/bold] {stats['hold_count']}\n"
            f"[bold]Avg Confidence:[/bold] {stats['avg_confidence']:.2f}\n"
            f"[bold]Avg Score:[/bold] {stats['avg_score']:.1f}\n"
            f"[bold]Evaluated Outcomes:[/bold] {stats['evaluated_outcomes']}\n"
            f"[bold]Successful:[/bold] {stats['successful_outcomes']}\n"
            f"[bold]Success Rate:[/bold] {stats['success_rate_pct']:.1f}%\n"
            f"[bold]Avg Actual Return:[/bold] {stats['avg_actual_return']:.2f}%\n"
            f"[bold]Avg Expected Return:[/bold] {stats['avg_expected_return']:.2f}%\n"
            f"[bold]Avg Time to Target:[/bold] {stats['avg_time_to_target_days']:.1f} days",
            title="Overall Performance"
        )
        console.print(panel)

    if symbol:
        stats = db.get_symbol_performance(symbol.upper())
        panel = Panel.fit(
            f"[bold]Recommendations:[/bold] {stats['recommendation_count']}\n"
            f"[bold]Avg Confidence:[/bold] {stats['avg_confidence']:.2f}\n"
            f"[bold]Avg Score:[/bold] {stats['avg_score']:.1f}\n"
            f"[bold]Evaluated:[/bold] {stats['evaluated']}\n"
            f"[bold]Successful:[/bold] {stats['successful']}\n"
            f"[bold]Success Rate:[/bold] {stats['success_rate_pct']:.1f}%\n"
            f"[bold]Avg Return:[/bold] {stats['avg_return']:.2f}%\n"
            f"[bold]Avg Time to Target:[/bold] {stats['avg_time_to_target']:.1f} days",
            title=f"Performance: {symbol.upper()}"
        )
        console.print(panel)

    if not overall and not symbol:
        monthly = db.get_monthly_stats(months)
        table = Table(title=f"Monthly Stats (last {months} months)")
        table.add_column("Month")
        table.add_column("Symbols")
        table.add_column("Total Recs")
        table.add_column("Buy")
        table.add_column("Sell")
        table.add_column("Hold")
        table.add_column("Avg Conf")
        table.add_column("Avg Score")
        table.add_column("Evaluated")
        table.add_column("Success")
        table.add_column("Success %")
        table.add_column("Avg Actual %")
        table.add_column("Avg Expected %")
        table.add_column("Avg Days")

        for m in monthly:
            table.add_row(
                m.month, str(m.unique_symbols), str(m.total_recommendations),
                str(m.buy_count), str(m.sell_count), str(m.hold_count),
                f"{m.avg_confidence:.2f}", f"{m.avg_score:.1f}",
                str(m.evaluated_outcomes), str(m.successful_outcomes),
                f"{m.success_rate_pct:.1f}%",
                f"{m.avg_actual_return:.2f}" if m.avg_actual_return else "—",
                f"{m.avg_expected_return:.2f}" if m.avg_expected_return else "—",
                f"{m.avg_time_to_target_days:.1f}" if m.avg_time_to_target_days else "—"
            )
        console.print(table)


@app.command()
def export(
    output: str = typer.Option("learning_export.json", "--output", "-o"),
):
    """Export all data to JSON."""
    db = get_db()
    import json

    recs = db.get_recommendations(limit=10000)
    outcomes = db.get_outcomes(limit=10000)

    data = {
        "recommendations": [
            {
                "id": r.id, "symbol": r.symbol, "date": r.date,
                "confidence": r.confidence, "recommendation": r.recommendation,
                "score": r.score, "factors_hash": r.factors_hash,
                "timestamp": r.timestamp, "created_at": r.created_at
            } for r in recs
        ],
        "outcomes": [
            {
                "id": o.id, "symbol": o.symbol, "date": o.date,
                "market_outcome": o.market_outcome, "expected_return": o.expected_return,
                "actual_return": o.actual_return, "time_to_target": o.time_to_target,
                "success": o.success, "evaluated_at": o.evaluated_at,
                "created_at": o.created_at
            } for o in outcomes
        ],
        "exported_at": datetime.now().isoformat()
    }

    with open(output, "w") as f:
        json.dump(data, f, indent=2)
    console.print(f"[green]✓[/green] Exported {len(recs)} recommendations and {len(outcomes)} outcomes to {output}")


# ============================================================
# Outcome Recording Commands (New)
# ============================================================

@app.command()
def record_price(
    symbol: str,
    price: float = typer.Argument(..., help="Closing price"),
    date: str = typer.Option(None, "--date", "-d", help="Date (YYYY-MM-DD), default today"),
    high: Optional[float] = typer.Option(None, "--high", help="High price"),
    low: Optional[float] = typer.Option(None, "--low", help="Low price"),
    volume: Optional[float] = typer.Option(None, "--volume", "-v", help="Volume"),
    source: str = typer.Option("manual", "--source", help="Price source"),
):
    """Record a daily closing price for a symbol."""
    recorder = get_recorder()
    d = date or datetime.now().strftime("%Y-%m-%d")
    close = recorder.record_daily_close(
        symbol=symbol,
        date=d,
        close_price=price,
        high_price=high,
        low_price=low,
        volume=volume,
        source=source,
    )
    console.print(f"[green]✓[/green] Recorded {close.symbol} @ {close.date}: {close.close_price}")


@app.command()
def fetch_prices(
    symbols: List[str] = typer.Argument(None, help="Symbols to fetch (default: all configured)"),
):
    """Fetch latest prices from market service and record as daily closes."""
    if symbols is None:
        from trading import config
        symbols = config.PAIRS
    
    results = record_daily_closes_from_market_service(symbols)
    
    table = Table(title="Recorded Daily Closes")
    table.add_column("Symbol")
    table.add_column("Date")
    table.add_column("Close")
    table.add_column("Source")
    
    for sym, close in results.items():
        table.add_row(sym, close.date, str(close.close_price), close.source)
    
    console.print(table)
    console.print(f"[green]✓[/green] Recorded {len(results)} prices")


@app.command()
def track_rec(
    rec_id: int,
    expected_return: Optional[float] = typer.Option(None, "--expected", "-e", help="Expected return %"),
    target: Optional[float] = typer.Option(None, "--target", "-t", help="Target price"),
    stop: Optional[float] = typer.Option(None, "--stop", "-s", help="Stop loss price"),
    holding_days: int = typer.Option(30, "--days", "-d", help="Expected holding period (days)"),
):
    """Create an outcome tracking record from a recommendation."""
    recorder = get_recorder()
    outcome = recorder.create_outcome_from_recommendation(
        recommendation_id=rec_id,
        expected_return_pct=expected_return,
        target_price=target,
        stop_loss=stop,
        holding_period_days=holding_days,
    )
    console.print(f"[green]✓[/green] Tracking outcome for recommendation #{rec_id}: {outcome.symbol} {outcome.recommendation_type}")
    console.print(f"  Expected return: {outcome.expected_return_pct}%")
    console.print(f"  Target price: {outcome.target_price}")
    console.print(f"  Stop loss: {outcome.stop_loss}")
    console.print(f"  Holding period: {outcome.holding_period_days} days")


@app.command()
def evaluate(
    rec_id: Optional[int] = typer.Argument(None, help="Recommendation ID (omit to evaluate all pending)"),
    date: str = typer.Option(None, "--date", "-d", help="Evaluation date (YYYY-MM-DD), default today"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-evaluate already evaluated outcomes"),
    max_days: Optional[int] = typer.Option(None, "--max-days", help="Max holding days for batch evaluation"),
):
    """Evaluate recommendation outcome(s) using recorded price data."""
    recorder = get_recorder()
    eval_date = date or datetime.now().strftime("%Y-%m-%d")
    
    if rec_id is not None:
        outcome = recorder.evaluate_outcome(rec_id, eval_date, force=force)
        if outcome.evaluated_at:
            _print_outcome_result(outcome)
        else:
            console.print(f"[yellow]⚠[/yellow] Not enough price data to evaluate recommendation #{rec_id}")
    else:
        outcomes = recorder.evaluate_all_pending(eval_date, max_holding_days=max_days)
        console.print(f"[green]✓[/green] Evaluated {len(outcomes)} outcomes")
        for o in outcomes:
            _print_outcome_result(o)


@app.command()
def show_outcome(
    rec_id: int = typer.Argument(..., help="Recommendation ID"),
):
    """Show detailed outcome for a recommendation."""
    recorder = get_recorder()
    outcome = recorder.get_outcome(rec_id)
    
    if not outcome:
        console.print(f"[red]✗[/red] No outcome tracking for recommendation #{rec_id}")
        return
    
    _print_outcome_detail(outcome)


@app.command()
def list_tracked(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s"),
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
    unevaluated: bool = typer.Option(False, "--unevaluated", "-u", help="Show only unevaluated"),
    limit: int = typer.Option(20, "--limit", "-l"),
):
    """List tracked recommendation outcomes."""
    recorder = get_recorder()
    outcomes = recorder.get_outcomes(
        symbol=symbol,
        start_date=start,
        end_date=end,
        evaluated_only=not unevaluated,
        limit=limit,
    )
    
    table = Table(title="Tracked Outcomes")
    table.add_column("Rec ID", style="dim")
    table.add_column("Symbol")
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Expected %")
    table.add_column("Actual %")
    table.add_column("Market")
    table.add_column("Success")
    table.add_column("Days to Target")
    table.add_column("Evaluated")
    
    for o in outcomes:
        table.add_row(
            str(o.recommendation_id),
            o.symbol,
            o.recommendation_date,
            o.recommendation_type,
            f"{o.expected_return_pct:.1f}" if o.expected_return_pct else "—",
            f"{o.actual_return_pct:.1f}" if o.actual_return_pct else "—",
            o.market_outcome or "—",
            "✓" if o.success else ("✗" if o.success is not None else "—"),
            str(o.time_to_target_days) if o.time_to_target_days else "—",
            o.evaluated_at[:19] if o.evaluated_at else "—",
        )
    console.print(table)


@app.command()
def performance(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s"),
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
):
    """Show performance summary statistics."""
    recorder = get_recorder()
    stats = recorder.get_performance_summary(symbol, start, end)
    
    if stats.get("total_evaluated", 0) == 0:
        console.print("[yellow]No evaluated outcomes found[/yellow]")
        return
    
    title = f"Performance Summary" + (f" — {symbol.upper()}" if symbol else "")
    panel = Panel.fit(
        f"[bold]Total Evaluated:[/bold] {stats['total_evaluated']}\n"
        f"[bold]Successful:[/bold] {stats['successful']}  "
        f"[bold]Failed:[/bold] {stats['failed']}\n"
        f"[bold]Success Rate:[/bold] {stats['success_rate_pct']:.1f}%\n"
        f"[bold]Avg Return:[/bold] {stats['avg_return_pct']:.2f}%\n"
        f"[bold]Median Return:[/bold] {stats['median_return_pct']:.2f}%\n"
        f"[bold]Best Trade:[/bold] {stats['best_trade_pct']:.2f}%\n"
        f"[bold]Worst Trade:[/bold] {stats['worst_trade_pct']:.2f}%\n"
        f"[bold]Avg Time to Target:[/bold] {stats['avg_time_to_target_days'] or '—'} days\n\n"
        f"[bold]By Recommendation Type:[/bold]\n" +
        "\n".join(
            f"  {k}: {v['total']} trades, {v['success_rate_pct']:.1f}% win, {v['avg_return_pct']:.2f}% avg"
            for k, v in stats['by_recommendation_type'].items()
        ) + "\n\n"
        f"[bold]By Market Outcome:[/bold]\n" +
        "\n".join(f"  {k}: {v}" for k, v in stats['by_market_outcome'].items()),
        title=title,
    )
    console.print(panel)


@app.command()
def symbol_perf(
    symbol: str = typer.Argument(..., help="Symbol to analyze"),
    limit: int = typer.Option(50, "--limit", "-l"),
):
    """Show detailed outcomes for a specific symbol."""
    recorder = get_recorder()
    outcomes = recorder.get_symbol_outcomes(symbol, limit)
    
    if not outcomes:
        console.print(f"[yellow]No outcomes for {symbol.upper()}[/yellow]")
        return
    
    table = Table(title=f"Outcomes for {symbol.upper()}")
    table.add_column("Rec ID", style="dim")
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Conf")
    table.add_column("Exp %")
    table.add_column("Act %")
    table.add_column("Market")
    table.add_column("Success")
    table.add_column("Days→Target")
    table.add_column("MFE %")
    table.add_column("MAE %")
    table.add_column("Evaluated")
    
    for o in outcomes:
        table.add_row(
            str(o["recommendation_id"]),
            o["date"],
            o["type"],
            f"{o['confidence']:.2f}",
            f"{o['expected_return']:.1f}" if o['expected_return'] else "—",
            f"{o['actual_return']:.1f}" if o['actual_return'] else "—",
            o["market_outcome"] or "—",
            "✓" if o["success"] else ("✗" if o["success"] is not None else "—"),
            str(o["time_to_target"]) if o["time_to_target"] else "—",
            f"{o['max_favorable_pct']:.1f}" if o["max_favorable_pct"] else "—",
            f"{o['max_adverse_pct']:.1f}" if o["max_adverse_pct"] else "—",
            o["evaluated_at"][:19] if o["evaluated_at"] else "—",
        )
    console.print(table)


def _print_outcome_result(outcome: RecommendationOutcome):
    """Print a formatted outcome evaluation result."""
    success_style = "green" if outcome.success else "red"
    console.print(
        f"  Rec #{outcome.recommendation_id}: {outcome.symbol} {outcome.recommendation_type} "
        f"→ Market: [bold]{outcome.market_outcome}[/bold] | "
        f"Actual: [bold]{outcome.actual_return_pct:.2f}%[/bold] | "
        f"Success: [{success_style}]{'✓' if outcome.success else '✗'}[/{success_style}]"
    )
    if outcome.time_to_target_days:
        console.print(f"    Time to target: {outcome.time_to_target_days} days")
    if outcome.max_favorable_excursion_pct:
        console.print(f"    Max favorable excursion: {outcome.max_favorable_excursion_pct:.2f}%")
    if outcome.max_adverse_excursion_pct:
        console.print(f"    Max adverse excursion: {outcome.max_adverse_excursion_pct:.2f}%")


def _print_outcome_detail(outcome: RecommendationOutcome):
    """Print detailed outcome information."""
    mfe = outcome.max_favorable_excursion_pct if outcome.max_favorable_excursion_pct is not None else 0.0
    mae = outcome.max_adverse_excursion_pct if outcome.max_adverse_excursion_pct is not None else 0.0
    panel = Panel.fit(
        f"[bold]Recommendation ID:[/bold] {outcome.recommendation_id}\n"
        f"[bold]Symbol:[/bold] {outcome.symbol}\n"
        f"[bold]Date:[/bold] {outcome.recommendation_date}\n"
        f"[bold]Type:[/bold] {outcome.recommendation_type}\n"
        f"[bold]Confidence:[/bold] {outcome.confidence:.2f}\n"
        f"[bold]Expected Return:[/bold] {outcome.expected_return_pct if outcome.expected_return_pct is not None else '—'}%\n"
        f"[bold]Target Price:[/bold] {outcome.target_price if outcome.target_price is not None else '—'}\n"
        f"[bold]Stop Loss:[/bold] {outcome.stop_loss if outcome.stop_loss is not None else '—'}\n"
        f"[bold]Holding Period:[/bold] {outcome.holding_period_days} days\n\n"
        f"[bold]Market Outcome:[/bold] {outcome.market_outcome or '—'}\n"
        f"[bold]Actual Return:[/bold] {outcome.actual_return_pct if outcome.actual_return_pct is not None else '—'}%\n"
        f"[bold]Time to Target:[/bold] {outcome.time_to_target_days or '—'} days\n"
        f"[bold]Time to Stop:[/bold] {outcome.time_to_stop_loss_days or '—'} days\n"
        f"[bold]Success:[/bold] {'✓' if outcome.success else '✗' if outcome.success is not None else '—'}\n"
        f"[bold]Max Favorable Excursion:[/bold] {mfe:.2f}%\n"
        f"[bold]Max Adverse Excursion:[/bold] {mae:.2f}%\n"
        f"[bold]Evaluated At:[/bold] {outcome.evaluated_at or '—'}\n"
        f"[bold]Evaluation Date:[/bold] {outcome.evaluation_date or '—'}\n"
        f"[bold]Portfolio Value (Rec):[/bold] {outcome.portfolio_value_at_rec or '—'}\n"
        f"[bold]Portfolio Value (Eval):[/bold] {outcome.portfolio_value_at_eval or '—'}",
        title=f"Outcome Detail: {outcome.symbol}"
    )
    console.print(panel)


if __name__ == "__main__":
    app()