"""``trading`` — main Typer CLI entry point.

This is the user-facing command line for the Hermes Alpha trading engine.
Every command group is a thin wrapper around a service module — the CLI
contains no business logic, only argument parsing, service calls,
output formatting, and exit codes.

Usage
-----

    # As a module:
    python3 -m trading.cli.main <command> [options]

    # As a script (if installed):
    trading <command> [options]

The CLI is the public surface for the trading engine. The dashboard,
Telegram bot, and future REST endpoints all delegate to the same
service functions used here.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import typer

from .commands import (
    allocations as allocations_cmd,
    backtest_cmd,
    benchmark,
    compare,
    config as config_cmd,
    context,
    dashboard as dashboard_cmd,
    decision,
    doctor,
    explain,
    forecast,
    gap_scan,
    morning,
    monthly_report,
    opportunities,
    portfolio,
    price,
    rebalance,
    scan,
    signal,
    stats,
    strategies,
    summary,
    target_allocation as target_cmd,
    execute,
)

# ── App ─────────────────────────────────────────────────────────────────
app = typer.Typer(
    name="trading",
    help=(
        "Hermes Alpha trading engine — manage the entire platform from the terminal. "
        "Every command answers one question; --json on every command for machine output."
    ),
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


# ── Daily operations ────────────────────────────────────────────────────
@app.command("morning")
def morning_cmd(
    telegram: bool = typer.Option(False, "--telegram", help="Format output for Telegram (legacy layout)."),
    save: bool = typer.Option(False, "--save", help="Save the briefing to ~/.trading/logs/morning-YYYY-MM-DD.md."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write the briefing to FILE."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit a JSON document on stdout."),
) -> None:
    """Complete morning investment briefing."""
    raise typer.Exit(morning.run(
        telegram=telegram, save=save, output_path=output, quiet=quiet, as_json=as_json,
    ))


@app.command("summary")
def summary_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="One-line per asset."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show more detail."),
) -> None:
    """Concise market overview — top picks, market score, no detail."""
    raise typer.Exit(summary.run(quiet=quiet, as_json=as_json, verbose=verbose))


@app.command("scan")
def scan_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-pair debug info."),
) -> None:
    """Run a complete market scan over every configured pair."""
    raise typer.Exit(scan.run(quiet=quiet, as_json=as_json, verbose=verbose))


# ── Asset commands ──────────────────────────────────────────────────────
@app.command("signal")
def signal_cmd(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM or EURUSD."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show raw RSI, SMA, MACD values."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
) -> None:
    """Show the recommendation + score for one symbol."""
    raise typer.Exit(signal.run(symbol=symbol, quiet=quiet, as_json=as_json, verbose=verbose, output_path=output))


@app.command("explain")
def explain_cmd(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. KCB."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Also print raw indicator values (RSI, score, factors).",
    ),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
) -> None:
    """Plain-English explanation of the recommendation for one symbol."""
    raise typer.Exit(
        explain.run(symbol=symbol, quiet=quiet, as_json=as_json, verbose=verbose, output_path=output)
    )


# ── Market intelligence ───────────────────────────────────────────
@app.command("context")
def context_cmd(
    symbol: Optional[str] = typer.Argument(
        None, help="Asset symbol, e.g. SCOM. Omit when using --market."),
    market: bool = typer.Option(
        False, "--market",
        help="Show top macro events for the next 30 days instead of a single symbol.",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show what's driving an asset (or the market) — news, macro, earnings."""
    raise typer.Exit(
        context.run(symbol=symbol, market=market, as_json=as_json)
    )


@app.command("price")
def price_cmd(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show 7-day history."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
) -> None:
    """Latest price, daily change, trend, volatility, and source."""
    raise typer.Exit(price.run(symbol=symbol, quiet=quiet, as_json=as_json, verbose=verbose, output_path=output))


@app.command("gap-scan")
def gap_scan_cmd(
    threshold: float = typer.Option(2.0, "--threshold", "-t",
                                    help="Minimum |gap| % to flag (default: 2.0)."),
    all_stocks: bool = typer.Option(False, "--all", "-a",
                                    help="Show ALL NSE movers, not just watchlist."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    cron: bool = typer.Option(False, "--cron",
                               help="Silently skip outside trading hours (08:30-14:00 EAT Mon-Fri)."),
) -> None:
    """Scan NSE for stocks that gapped from yesterday's close."""
    raise typer.Exit(gap_scan.run(
        threshold=threshold, all_stocks=all_stocks, quiet=quiet, as_json=as_json, cron=cron,
    ))


# ── Forecast command ──────────────────────────────────────────────────
@app.command("forecast")
def forecast_cmd(
    symbol: str = typer.Argument(..., help="Stock symbol (e.g. SCOM)"),
    days: int = typer.Option(5, "--days", "-d",
                              help="Trading days to project (1-7, default: 5)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Statistical price forecast — projects a range for the next 1-7 days."""
    import subprocess, json
    result = subprocess.run(
        [sys.executable, "-m", "trading.cli.commands.forecast",
         symbol, "--days", str(days),
         *(["--json"] if as_json else [])],
        capture_output=True, text=True, timeout=30,
    )
    if as_json:
        print(result.stdout)
    else:
        print(result.stdout or result.stderr)
    raise typer.Exit(result.returncode)


# ── Opportunity commands ────────────────────────────────────────────────
@app.command("opportunities")
def opportunities_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    save: bool = typer.Option(False, "--save", help="Save to ~/.trading/logs/opportunities-YYYY-MM-DD.md."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write to FILE."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show factor breakdown per asset."),
) -> None:
    """Rank every monitored asset (highest score first)."""
    rc = opportunities.run(quiet=quiet, as_json=as_json, verbose=verbose)
    if (save or output) and not as_json:
        # Best-effort save: re-fetch ranking and write plain text
        from .. import config
        from ..services import ranking as ranking_svc
        result = ranking_svc.build()
        ranked = result.get("ranked", [])
        body = "\n".join(
            f"#{r.get('rank', 0):<2d}  {r['symbol']:<8s}  {r['score']:5.1f}  "
            f"{r['recommendation']:<18s}  {r.get('holding_period', '')}"
            for r in ranked
        )
        path = output or os.path.join(
            config.LOGS_DIR, f"opportunities-{_today()}.md"
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(body + "\n")
        except OSError as exc:
            print(f"⚠️  Could not save: {exc}", file=sys.stderr)
    raise typer.Exit(rc)


def _today() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Strategy commands ───────────────────────────────────────────────────
@app.command("strategies")
def strategies_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full strategy config."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
) -> None:
    """List every registered strategy with status (Benchmark/Experimental)."""
    raise typer.Exit(strategies.run(quiet=quiet, as_json=as_json, verbose=verbose, output_path=output))


@app.command("benchmark")
def benchmark_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full benchmark logic."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
) -> None:
    """Show details of the frozen benchmark strategy (Strategy A)."""
    raise typer.Exit(benchmark.run(quiet=quiet, as_json=as_json, verbose=verbose, output_path=output))


@app.command("compare")
def compare_cmd(
    pairs: Optional[str] = typer.Option(None, "--pairs", help="Comma-separated pairs (default: all)."),
    years: float = typer.Option(2.0, "--years", help="Years of history per pair."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-pair comparison."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
) -> None:
    """Compare all strategies side-by-side: return, drawdown, Sharpe, win rate, verdict."""
    raise typer.Exit(compare.run(pairs=pairs, years=years, quiet=quiet, as_json=as_json, verbose=verbose, output_path=output))


@app.command("backtest")
def backtest_cmd_(
    strategy: str = typer.Option("A", "--strategy", "-s", help="Strategy key (default: A)."),
    pair: Optional[str] = typer.Option(None, "--pair", "-p", help="Single pair (default: all)."),
    years: float = typer.Option(2.0, "--years", help="Years of history per pair."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-pair detail."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
) -> None:
    """Run a backtest (default: benchmark strategy on all pairs)."""
    raise typer.Exit(backtest_cmd.run(
        strategy=strategy, pair=pair, years=years, quiet=quiet, as_json=as_json,
        verbose=verbose, output_path=output,
    ))


# ── Execution Engine ─────────────────────────────────────────────────
execute_app = typer.Typer(help="Execute trades through the safety-checked execution engine.")
app.add_typer(execute_app, name="execute")


@execute_app.command("check")
def execute_check(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: int = typer.Option(0, "--shares", "-n", help="Number of shares to check."),
    side: str = typer.Option("BUY", "--side", help="BUY or SELL."),
    price: Optional[float] = typer.Option(None, "--price", help="Price per share (default: live price)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Dry-run safety check for a potential trade."""
    raise typer.Exit(execute.check_cmd(
        symbol=symbol, shares=shares, side=side, price=price, quiet=quiet, as_json=as_json
    ))


@execute_app.command("buy")
def execute_buy(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: int = typer.Option(0, "--shares", "-n", help="Number of shares to buy."),
    all: bool = typer.Option(False, "--all", help="Buy as many shares as cash allows."),
    price: Optional[float] = typer.Option(None, "--price", help="Price per share (default: live price)."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional trade reason (default: signal)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Execute a BUY through the safety layer."""
    raise typer.Exit(execute.buy_cmd(
        symbol=symbol, shares=shares, all=all, price=price, reason=reason, quiet=quiet, as_json=as_json
    ))


@execute_app.command("sell")
def execute_sell(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: Optional[int] = typer.Option(None, "--shares", "-n", help="Shares to sell (omit to sell all)."),
    price: Optional[float] = typer.Option(None, "--price", help="Price per share (default: live price)."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional trade reason."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Execute a SELL through the safety layer."""
    raise typer.Exit(execute.sell_cmd(
        symbol=symbol, shares=shares, price=price, reason=reason, quiet=quiet, as_json=as_json
    ))


@execute_app.command("status")
def execute_status(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Show execution engine status (broker + safety)."""
    raise typer.Exit(execute.status_cmd(quiet=quiet, as_json=as_json))


@execute_app.command("emergency-stop")
def execute_emergency_stop(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Activate emergency stop — no more trades."""
    raise typer.Exit(execute.emergency_stop_cmd(quiet=quiet, as_json=as_json))


@execute_app.command("release")
def execute_release(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Release emergency stop — resume trading."""
    raise typer.Exit(execute.release_cmd(quiet=quiet, as_json=as_json))


@execute_app.command("override")
def execute_override(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    block: bool = typer.Option(False, "--block", help="Block all trades for this symbol."),
    force: bool = typer.Option(False, "--force", help="Force allow trades for this symbol."),
    clear: bool = typer.Option(False, "--clear", help="Remove override for this symbol."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Set manual override (block/force) for a symbol."""
    raise typer.Exit(execute.override_cmd(
        symbol=symbol, block=block, force=force, clear=clear, quiet=quiet, as_json=as_json
    ))


@execute_app.command("reset-daily")
def execute_reset_daily(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Manually reset daily counters."""
    raise typer.Exit(execute.reset_daily_cmd(quiet=quiet, as_json=as_json))


@execute_app.command("plan")
def execute_plan(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Generate a trade plan from the current allocation proposal — does NOT execute."""
    raise typer.Exit(execute.plan_cmd(quiet=quiet, as_json=as_json))


@execute_app.command("deploy")
def execute_deploy(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Execute the current trade plan from allocation (safety-checked)."""
    raise typer.Exit(execute.deploy_cmd(quiet=quiet, as_json=as_json))


# ── Portfolio Manager ───────────────────────────────────────────────────
portfolio_app = typer.Typer(help="Paper portfolio manager — track positions, P&L, snapshots.")
app.add_typer(portfolio_app, name="portfolio")


@portfolio_app.command("init")
def portfolio_init(
    capital: float = typer.Option(100000.0, "--capital", help="Initial capital in KES."),
    force: bool = typer.Option(False, "--force", help="Wipe existing portfolio and start fresh."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create (or reset with --force) the paper portfolio."""
    raise typer.Exit(portfolio.init_cmd(capital=capital, force=force, as_json=as_json, quiet=quiet))


@portfolio_app.command("show")
def portfolio_show(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the current portfolio: cash, positions, P&L, drawdown, benchmark."""
    raise typer.Exit(portfolio.show_cmd(as_json=as_json, quiet=quiet))


@portfolio_app.command("buy")
def portfolio_buy(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: int = typer.Option(0, "--shares", help="Number of shares to buy."),
    all_in: bool = typer.Option(False, "--all", help="Buy as many shares as cash allows."),
    price: Optional[float] = typer.Option(None, "--price", help="Override the live price (for testing)."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Record a paper BUY. Uses the live signal as the trade reason."""
    if not all_in and shares <= 0:
        raise typer.BadParameter("Provide --shares N or --all")
    raise typer.Exit(portfolio.buy_cmd(
        symbol=symbol, shares=shares, all_in=all_in, price_override=price,
        as_json=as_json, quiet=quiet,
    ))


@portfolio_app.command("sell")
def portfolio_sell(
    symbol: str = typer.Argument(..., help="Asset symbol, e.g. SCOM."),
    shares: Optional[int] = typer.Option(None, "--shares", help="Shares to sell (omit to sell entire position)."),
    price: Optional[float] = typer.Option(None, "--price", help="Override the live price (for testing)."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Record a paper SELL. Reports realised P&L. Omit --shares to sell all."""
    raise typer.Exit(portfolio.sell_cmd(
        symbol=symbol, shares=shares, price_override=price,
        as_json=as_json, quiet=quiet,
    ))


@portfolio_app.command("snapshot")
def portfolio_snapshot(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Take a mark-to-market snapshot (designed to run on a cron)."""
    raise typer.Exit(portfolio.snapshot_cmd(as_json=as_json, quiet=quiet))


@portfolio_app.command("history")
def portfolio_history(
    days: int = typer.Option(90, "--days", help="Window to chart (default: 90)."),
    as_csv: bool = typer.Option(False, "--csv", help="Emit CSV (snapshots) to stdout."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show portfolio value history (ASCII chart + benchmark overlay)."""
    raise typer.Exit(portfolio.history_cmd(days=days, as_csv=as_csv, as_json=as_json, quiet=quiet))


@portfolio_app.command("decisions")
def portfolio_decisions(
    last: Optional[int] = typer.Option(None, "--last", "-n", help="Show only the last N trades."),
    symbol: Optional[str] = typer.Option(None, "--symbol", help="Filter to one symbol."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the trade ledger with reasoning and realised P&L."""
    raise typer.Exit(portfolio.decisions_cmd(last=last, symbol=symbol, as_json=as_json, quiet=quiet))


# ── Decision Engine ─────────────────────────────────────────────────────
@app.command("allocations")
def allocations_func(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Show target portfolio allocations (Phase-4 placeholder)."""
    raise typer.Exit(allocations_cmd.run(quiet=quiet, as_json=as_json))


@app.command("target")
def target_command(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    rebalance: bool = typer.Option(False, "--rebalance", help="Show rebalance trade plan."),
) -> None:
    """Show strategic sector-based target allocation vs current portfolio."""
    raise typer.Exit(target_cmd.run(quiet=quiet, as_json=as_json, show_rebalance=rebalance))


@app.command("rebalance")
def rebalance_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show raw position and target allocation data."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
    apply: bool = typer.Option(False, "--apply", help="Execute the suggested rebalance trades."),
) -> None:
    """Rebalance the portfolio: show current vs target allocations with drift analysis and trade suggestions."""
    raise typer.Exit(rebalance.run(
        quiet=quiet, verbose=verbose, as_json=as_json, output_path=output, apply=apply,
    ))


@app.command("decision")
def decision_cmd(
    tilt: Optional[str] = typer.Option(None, "--tilt", help="Override strategy tilt: Defensive / Balanced / Growth."),
    no_portfolio: bool = typer.Option(False, "--no-portfolio", help="Show theoretical allocation (ignore paper holdings)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-line reason column."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Holistic portfolio allocation recommendation."""
    raise typer.Exit(decision.run(tilt=tilt, no_portfolio=no_portfolio, verbose=verbose, as_json=as_json))


# ── Statistics / diagnostics / config ───────────────────────────────────
@app.command("stats")
def stats_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show raw data behind stats."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to FILE."),
) -> None:
    """Platform statistics: total signals, scans, win rate, best strategy, last scan."""
    raise typer.Exit(stats.run(quiet=quiet, as_json=as_json, verbose=verbose, output_path=output))


@app.command("doctor")
def doctor_cmd(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run a full health check (config, market data, yfinance, storage, logs, strategies)."""
    raise typer.Exit(doctor.run(quiet=quiet, as_json=as_json))


# Sub-app for `trading config {show,validate,edit}`
config_app = typer.Typer(help="Inspect, validate, and edit the configuration.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Emit compact JSON instead of formatted JSON."),
    as_json: bool = typer.Option(False, "--json", help="Emit a JSON document on stdout."),
) -> None:
    """Print the active configuration."""
    raise typer.Exit(config_cmd.show(quiet=quiet, as_json=as_json))


@config_app.command("validate")
def config_validate(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Emit JSON instead of formatted text."),
) -> None:
    """Validate the configuration (PAIRS, SMA, RSI, tickers)."""
    raise typer.Exit(config_cmd.validate_cmd(quiet=quiet))


@config_app.command("edit")
def config_edit() -> None:
    """Open the config file in the user's editor ($EDITOR, default nano)."""
    raise typer.Exit(config_cmd.edit())


# ── Dashboard ──────────────────────────────────────────────────────────
dashboard_app = typer.Typer(help="Trading supervision dashboard — PnL, signal quality, rule versions.")
app.add_typer(dashboard_app, name="dashboard")


@dashboard_app.callback(invoke_without_command=True)
def dashboard_default(
    ctx: typer.Context,
    html: bool = typer.Option(False, "--html", help="Output HTML instead of text."),
    no_telegram: bool = typer.Option(False, "--no-telegram", help="Don't send to Telegram."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write HTML to file."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Generate and display the supervision dashboard report."""
    if ctx.invoked_subcommand is not None:
        return
    raise typer.Exit(dashboard_cmd.run(
        html=html, no_telegram=no_telegram, output_path=output, quiet=quiet, as_json=as_json,
    ))


@dashboard_app.command("serve")
def dashboard_serve(
    port: int = typer.Option(9210, "--port", "-p", help="HTTP port."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output."),
) -> None:
    """Start a live web server serving the dashboard HTML (regenerates on each request)."""
    raise typer.Exit(dashboard_cmd.serve(port=port, quiet=quiet))


# ── Monthly Report ────────────────────────────────────────────────────────
@app.command("monthly-report")
def monthly_report_cmd(
    months: int = typer.Option(1, "--months", "-m", help="Months of history to include"),
    telegram: bool = typer.Option(False, "--telegram", "-t", help="Send to Telegram"),
    save: bool = typer.Option(False, "--save", "-s", help="Save to ~/.trading/logs/"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Custom output file"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Generate monthly performance report."""
    raise typer.Exit(monthly_report.run(
        months=months, telegram=telegram, save=save, output=output,
        quiet=quiet, as_json=as_json,
    ))


# ── Entry point ─────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    """Run the Typer app. Returns the process exit code."""
    try:
        app(args=argv, standalone_mode=False)
        return 0
    except SystemExit as exc:  # typer.Exit raises SystemExit
        return int(exc.code) if exc.code is not None else 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())