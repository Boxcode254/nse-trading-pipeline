"""``trading compare`` — side-by-side comparison of all strategies."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .. import output
from ... import config
from ...services import strategies as strat_svc


def run(
    pairs: str | None = None,
    years: float = 2.0,
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Compare every registered strategy over the selected pairs."""
    pair_list = pairs.split(",") if pairs else list(config.PAIRS)
    result = strat_svc.compare(pairs=pair_list, years=years)
    if as_json:
        print(output.json_dumps(result))
        return 0
    rows = result.get("rows", [])
    if not rows:
        if quiet:
            print("no results")
        else:
            print("⚠️  No comparison results — check data source.")
        return 1

    if quiet:
        for row in rows:
            print(
                f"{row['strategy']:<25s}  {row['pair']:<8s}  "
                f"return={row['return_pct']:+.2f}%  sharpe={row['sharpe']:+.3f}  "
                f"win={row['win_rate_pct']:.1f}%  verdict={row['verdict']}"
            )
        return 0

    console = Console()
    table = Table(
        title=f"🔬 Strategy Comparison ({len(pair_list)} pairs × {years}y)",
        show_header=True,
        header_style="bold",
    )
    for h in ("Strategy", "Pair", "Return%", "Sharpe", "DD%", "Win%", "Trades", "Verdict"):
        table.add_column(h, justify="right" if h not in ("Strategy", "Pair", "Verdict") else "left")
    for row in rows:
        ret = row["return_pct"]
        ret_style = "green" if ret >= 0 else "red"
        table.add_row(
            row["strategy"],
            row["pair"],
            f"[{ret_style}]{ret:+.2f}[/]",
            f"{row['sharpe']:+.3f}",
            f"{row['drawdown_pct']:.2f}",
            f"{row['win_rate_pct']:.1f}",
            str(row["total_trades"]),
            row["verdict"],
        )
    console.print(table)

    if result.get("regime"):
        console.print(f"\nMarket regime: {result['regime']}")
    return 0
