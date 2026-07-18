"""CLI entry point: ``python3 -m trading <subcommand>``.

Subcommands
-----------
run         Fetch all configured pairs, compute the latest signal, validate,
            log it, and print a Telegram-friendly report.
history     Print the last N (default 20) signals from signals.csv.
backtest    Replay the signal engine over a single pair and show win rate.
compare     Run all strategies over selected pairs and compare.
learn       Show the experiment log (knowledge accumulated from tests).
validators  List the active signal validation filters and their thresholds.
rank        Score every tracked asset 0-100 and print a ranked leaderboard.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from . import config
from . import report as report_mod
from .fetchers import fetch_data
from .backtest import run_backtest, format_backtest_results
from .backtest.fetch_history import fetch_history
from .backtest.engine import BacktestResult
from .ranking import build_ranking, output as ranking_output
from .strategies import REGISTRY as STRATEGY_REGISTRY, get_strategy, list_strategies
from .research.comparison import compare_strategies, format_multi_pair_comparison
from .research.experiments import record_experiment, list_experiments, format_experiment_summary
from .signals import engine as signal_engine
from .signals import validator as signal_validator
from .storage import log as storage_log


def cmd_run(_args: argparse.Namespace) -> int:
    """Fetch all configured pairs and log the latest signal for each.

    Also runs the ranking engine on the same fetched data and surfaces
    the "Top Opportunities" section in the daily report. This shifts
    the system from "per-asset signal" thinking to "where should I
    invest today?" cross-market ranking.
    """
    config.ensure_dirs()
    run_start = time.time()

    rejected: list[dict[str, Any]] = []
    run_pairs: list[str] = []
    run_ok: list[str] = []
    run_failed: list[str] = []
    run_sources: dict[str, str] = {}
    pair_signals: dict[str, dict[str, Any]] = {}
    # Accumulate all successfully-fetched DataFrames so we can rank
    # them at the end without an extra network pass.
    fetched_frames: dict[str, pd.DataFrame] = {}

    for pair in config.PAIRS:
        run_pairs.append(pair)
        try:
            df = fetch_data(pair)
        except Exception as exc:  # noqa: BLE001 -- last line of defense
            run_failed.append(pair)
            continue

        run_ok.append(pair)
        run_sources[pair] = df.attrs.get("source", "?")
        fetched_frames[pair] = df

        try:
            signals = signal_engine.generate_signals(df, pair=pair)
        except Exception as exc:  # noqa: BLE001
            continue

        if not signals:
            continue

        # ── Validate through the Signal Validator ────────────────
        accepted, pair_rejected = signal_validator.filter_signals(signals, df)
        rejected.extend(pair_rejected)

        if not accepted:
            continue

        current = accepted[-1]
        storage_log.log_signal(current)
        pair_signals[pair] = current

    # ── Build the cross-market ranking (every fetched asset) ────
    ranking_result: list[dict] = []
    if fetched_frames:
        try:
            ranking_result = build_ranking(fetched_frames)["ranked"]
        except Exception:  # noqa: BLE001
            ranking_result = []  # ranking is best-effort; never break the run

    # ── Generate the daily decision-board report ────────────────
    run_end = time.time()
    if pair_signals or ranking_result:
        print(report_mod.format_daily_report(
            pair_signals, rejected,
            run_start=run_start,
            run_end=run_end,
            ranking=ranking_result,
        ))
    else:
        print("\u26a0\ufe0f  No signals produced. Check your data source and try again.")
        return 1

    # ── Write run log to ~/.trading/logs/YYYY-MM-DD.json ──────
    run_log = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(run_end - run_start, 2),
        "pairs_scanned": len(run_pairs),
        "pairs_with_data": len(run_ok),
        "pairs_failed": len(run_failed),
        "signals_accepted": len(pair_signals),
        "signals_rejected": len(rejected),
        "latest_signals": _summarise_signals(list(pair_signals.values())),
        "rejected_signals": _summarise_signals(rejected),
        "data_sources": run_sources,
        "ranking": [
            {k: e[k] for k in ("symbol", "score", "recommendation", "holding_period")}
            for e in ranking_result[:8]
        ] if ranking_result else [],
    }
    _write_run_log(run_log)
    return 0


def _summarise_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compress a signal list to a compact summary for JSON logging."""
    out = []
    for s in signals:
        entry: dict[str, Any] = {
            "symbol": s.get("pair", "").replace("/", ""),
            "decision": s.get("signal", ""),
            "price": s.get("price"),
            "rsi": s.get("rsi"),
            "confidence": s.get("confidence", 0),
            "rejected_by": s.get("rejected_by", []),
        }
        if s.get("rejection_reasons"):
            entry["rejection_reasons"] = s["rejection_reasons"]
        out.append(entry)
    return out


def _write_run_log(run_log: dict[str, Any]) -> None:
    """Write a JSON run log to ``~/.trading/logs/YYYY-MM-DD.json``.

    Appends to an existing file for the same date (multiple runs per day).
    """
    log_path = os.path.join(
        config.LOGS_DIR,
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json",
    )
    try:
        if os.path.exists(log_path):
            with open(log_path) as f:
                data = json.load(f)
        else:
            data = []
        if not isinstance(data, list):
            data = []
        data.append(run_log)
        with open(log_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except OSError:
        pass  # best-effort; don't let logging break the run


def cmd_history(args: argparse.Namespace) -> int:
    """Print the last N signals from signals.csv."""
    rows = storage_log.read_history(args.last)
    print(report_mod.format_history(rows))
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """Run a full historical backtest on one or all pairs.

    Uses real historical data via yfinance (forex) and tvDatafeed (NSE stocks).
    Results include win rate, Sharpe, max drawdown, and a letter grade.
    """
    config.ensure_dirs()

    pairs = args.pairs.split(",") if args.pairs else config.PAIRS
    years = getattr(args, "years", 2)
    strategy = get_strategy(args.strategy) if args.strategy else None

    results = []
    for pair in pairs:
        print(f"Fetching history for {pair} ...", end=" ", flush=True)
        df = fetch_history(pair, years=years)
        if df.empty:
            print("⚠️  No data")
            results.append(BacktestResult(pair=pair))
            continue
        print(f"{len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")
        result = run_backtest(pair, df, strategy=strategy)
        results.append(result)

    print("\n" + format_backtest_results(results))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Run all registered strategies over selected pairs and compare."""
    config.ensure_dirs()

    pairs = args.pairs.split(",") if args.pairs else config.PAIRS
    years = getattr(args, "years", 2)
    strategies: dict[str, Any] = dict(STRATEGY_REGISTRY)

    backtest_fn = run_backtest  # partial: will be called with (pair, df, strategy)

    reports = []
    for pair in pairs:
        print(f"Fetching history for {pair} ...", end=" ", flush=True)
        df = fetch_history(pair, years=years)
        if df.empty:
            print("⚠️  No data")
            continue
        print(f"{len(df)} bars")
        report = compare_strategies(pair, df, strategies, backtest_fn)
        reports.append(report)

    print("\n" + format_multi_pair_comparison(reports))
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    """Show the experiment log (knowledge accumulated from tests)."""
    limit = getattr(args, "last", 10) if hasattr(args, "last") else 10
    experiments = list_experiments(limit=limit)
    print(format_experiment_summary(experiments))
    return 0


def cmd_validators(_args: argparse.Namespace) -> int:
    """List the active signal validators and their thresholds."""
    filters = signal_validator.describe_filters()
    if not filters:
        print("No active validators.")
        return 0

    lines = ["Active Signal Validators", "-" * 25]
    for f in filters:
        lines.append(f"  {f['name']:<25s}  {f['description']}")
    lines.append("")
    lines.append("Thresholds (config.py):")
    lines.append(f"  CONFIDENCE_MIN_RSI_DELTA = {config.CONFIDENCE_MIN_RSI_DELTA}")
    lines.append(f"  CONFIRM_MAX_SPREAD_FRAC  = {config.CONFIRM_MAX_SPREAD_FRAC}")
    for asset_cls, settings in config.ASSET_FILTERS.items():
        mv = settings.get("min_volume", 0)
        lines.append(f"  ASSET_FILTERS[{asset_cls}].min_volume = {mv:,.0f}")
    lines.append(f"  DUPLICATE_COOLDOWN_HOURS = {config.DUPLICATE_COOLDOWN_HOURS}h")
    print("\n".join(lines))
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    """Score every tracked asset 0-100 and print a ranked leaderboard.

    Each asset is fetched via the standard fetcher (yfinance → synthetic
    fallback) and then scored across 8 factors (trend, momentum,
    volatility, liquidity, relative strength, risk, market regime,
    technical alignment). Output is sorted by aggregate score, with
    the top 3 highlighted as the day's "best opportunities".

    Use ``--top N`` to limit the detail section, or ``--summary`` for
    just the leaderboard table.
    """
    config.ensure_dirs()
    pairs = args.pairs.split(",") if args.pairs else list(config.PAIRS)
    if not pairs:
        print("\u26a0\ufe0f  No pairs configured. Check config.PAIRS.")
        return 1

    frames: dict[str, pd.DataFrame] = {}
    fetch_failures: list[str] = []
    run_start = time.time()
    for pair in pairs:
        try:
            df = fetch_data(pair, days=config.LOOKBACK_DAYS)
        except Exception as exc:  # noqa: BLE001 -- last-line-of-defense
            fetch_failures.append(pair)
            continue
        if df is None or df.empty:
            fetch_failures.append(pair)
            continue
        frames[pair] = df

    if not frames:
        print("\u26a0\ufe0f  Could not fetch data for any asset.")
        return 1

    summary = build_ranking(frames)
    ranked = summary["ranked"]

    # ── Print output ────────────────────────────────────────────────
    top_n = max(1, int(getattr(args, "top", 5)))
    summary_only = bool(getattr(args, "summary", False))

    if summary_only:
        print(ranking_output.format_ranking_summary(ranked))
    else:
        print(ranking_output.format_full_ranking_report(
            ranked, summary["weights"]
        ))

    run_end = time.time()

    # ── Per-run JSON log ────────────────────────────────────────────
    run_log = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(run_end - run_start, 2),
        "command": "rank",
        "pairs_scanned": len(pairs),
        "pairs_ranked": len(frames),
        "fetch_failures": fetch_failures,
        "weights": summary["weights"],
        "top_3": [
            {k: entry[k] for k in ("symbol", "score", "recommendation", "holding_period")}
            for entry in ranked[:3]
        ],
    }
    _write_run_log(run_log)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m trading",
        description="Forex signal engine -- learning tool, no real trading.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Fetch all pairs, log signals, print report")
    p_run.set_defaults(func=cmd_run)

    p_hist = sub.add_parser("history", help="Show recent signals from signals.csv")
    p_hist.add_argument("--last", type=int, default=20, help="How many rows to show")
    p_hist.set_defaults(func=cmd_history)

    p_bt = sub.add_parser("backtest", help="Replay a strategy over history")
    p_bt.add_argument(
        "--pairs", default=None,
        help="Comma-separated pairs (default: all configured pairs)"
    )
    p_bt.add_argument(
        "--years", type=float, default=2,
        help="Years of history per pair (default: 2)"
    )
    p_bt.add_argument(
        "--strategy", default=None,
        help="Strategy key: A (SMA/RSI), C (trend filter). Default: A"
    )
    p_bt.set_defaults(func=cmd_backtest)

    p_cmp = sub.add_parser("compare", help="Compare all strategies side-by-side")
    p_cmp.add_argument(
        "--pairs", default=None,
        help="Comma-separated pairs (default: all configured pairs)"
    )
    p_cmp.add_argument(
        "--years", type=float, default=2,
        help="Years of history per pair (default: 2)"
    )
    p_cmp.set_defaults(func=cmd_compare)

    p_learn = sub.add_parser("learn", help="Show experiment log (accumulated knowledge)")
    p_learn.add_argument("--last", type=int, default=10, help="How many experiments to show")
    p_learn.set_defaults(func=cmd_learn)

    p_val = sub.add_parser("validators", help="List active signal validation filters")
    p_val.set_defaults(func=cmd_validators)

    p_rank = sub.add_parser(
        "rank",
        help="Score every tracked asset 0-100 and print a ranked leaderboard",
    )
    p_rank.add_argument(
        "--pairs", default=None,
        help="Comma-separated pairs (default: all configured pairs)",
    )
    p_rank.add_argument(
        "--top", type=int, default=5,
        help="Number of top assets to show detailed factor breakdown (default: 5)",
    )
    p_rank.add_argument(
        "--summary", action="store_true",
        help="Print only the leaderboard table (skip per-asset factor detail)",
    )
    p_rank.set_defaults(func=cmd_rank)

    # ── allocate ─────────────────────────────────────────────────────
    p_alloc = sub.add_parser(
        "allocate",
        help="Generate portfolio allocation proposal based on signal scores",
    )
    p_alloc.add_argument(
        "--json", action="store_true",
        help="Output raw JSON instead of human-readable table",
    )
    p_alloc.set_defaults(func=cmd_allocate)

    return parser


def cmd_allocate(args: argparse.Namespace) -> int:
    """Generate and display the portfolio allocation proposal."""
    try:
        from .allocation import main as alloc_main
        # Override sys.argv so argument parsing inside allocation.main works
        sys.argv = ["allocate"]
        if args.json:
            sys.argv.append("--json")
        alloc_main()
        return 0
    except Exception as e:
        print(f"Allocation engine failed: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
