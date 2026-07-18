"""``trading backtest [strategy]`` — run backtests."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .. import output
from ... import config
from ...services import backtest as bt_svc


def run(
    strategy: str = "A",
    pair: str | None = None,
    years: float = 2.0,
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Run a backtest for *strategy* (default A) on *pair* or all pairs."""
    result = bt_svc.run(strategy=strategy, pair=pair, years=years)

    if as_json:
        print(output.json_dumps(result))
        return 0

    if pair:
        return _print_single(result, quiet=quiet)
    return _print_multi(result, quiet=quiet)


def _print_single(result: dict, quiet: bool) -> int:
    if "error" in result:
        print(f"⚠️  {result.get('pair', '?')}: {result['error']}")
        return 1
    if quiet:
        print(
            f"{result['pair']:<8s}  return={result['total_return_pct']:+.2f}%  "
            f"sharpe={result['sharpe_ratio']:+.3f}  win={result['win_rate_pct']:.1f}%  "
            f"trades={result['total_trades']}"
        )
        return 0

    console = Console()
    console.print(f"\n📊 Backtest: {result['strategy']} on {result['pair']}")
    if result.get("data_start") and result.get("data_end"):
        console.print(f"   Period:    {result['data_start']} → {result['data_end']}")
    table = Table(show_header=False, box=None)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    for label, key in [
        ("Total return", "total_return_pct"),
        ("Annualised", "annualised_return_pct"),
        ("Max drawdown", "max_drawdown_pct"),
        ("Sharpe", "sharpe_ratio"),
        ("Sortino", "sortino_ratio"),
        ("Win rate", "win_rate_pct"),
        ("Profit factor", "profit_factor"),
        ("Volatility", "volatility_pct"),
        ("Calmar", "calmar_ratio"),
        ("Trades", "total_trades"),
        ("Avg bars held", "avg_bars_held"),
        ("Buy & hold", "buy_and_hold_return_pct"),
    ]:
        v = result.get(key)
        if v is None:
            continue
        suffix = "%" if "pct" in key or key in ("win_rate_pct",) else ""
        table.add_row(label, f"{v}{suffix}")
    console.print(table)
    return 0


def _print_multi(result: dict, quiet: bool) -> int:
    per_pair = result.get("per_pair", [])
    if not per_pair:
        print("⚠️  No backtest results.")
        return 1
    if quiet:
        for r in per_pair:
            if "error" in r:
                print(f"{r['pair']:<8s}  error={r['error']}")
                continue
            print(
                f"{r['pair']:<8s}  return={r['total_return_pct']:+.2f}%  "
                f"sharpe={r['sharpe_ratio']:+.3f}  trades={r['total_trades']}"
            )
        return 0
    console = Console()
    table = Table(
        title=f"📊 Backtest: {result['strategy']} on all pairs",
        show_header=True,
        header_style="bold",
    )
    for h in ("Pair", "Return%", "Sharpe", "DD%", "Win%", "Trades"):
        table.add_column(h, justify="right" if h != "Pair" else "left")
    for r in per_pair:
        if "error" in r:
            table.add_row(r["pair"], f"[red]{r['error']}[/]", "-", "-", "-", "-")
            continue
        ret = r["total_return_pct"]
        ret_style = "green" if ret >= 0 else "red"
        table.add_row(
            r["pair"],
            f"[{ret_style}]{ret:+.2f}[/]",
            f"{r['sharpe_ratio']:+.3f}",
            f"{r['max_drawdown_pct']:.2f}",
            f"{r['win_rate_pct']:.1f}",
            str(r["total_trades"]),
        )
    console.print(table)
    return 0
