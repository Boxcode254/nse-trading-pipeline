"""``trading stats`` — platform statistics."""
from __future__ import annotations

from rich.console import Console

from .. import output
from ...services import stats as stats_svc


def run(quiet: bool = False, as_json: bool = False, verbose: bool = False, output_path: str | None = None) -> int:
    """Show platform stats: signal counts, scan counts, etc."""
    result = stats_svc.build()
    if as_json:
        print(output.json_dumps(result))
        return 0
    if quiet:
        print(
            f"signals={result['total_signals']} scans={result['total_scans']} "
            f"buy={result['buy_signals']} sell={result['sell_signals']} "
            f"win%={result['win_rate_pct']:.1f} best={result['best_strategy']}"
        )
        return 0
    console = Console()
    console.print("\n📊 Platform Stats")
    console.print(f"   Total signals logged:    {result['total_signals']}")
    console.print(f"   BUY signals:            {result['buy_signals']}")
    console.print(f"   SELL signals:           {result['sell_signals']}")
    console.print(f"   HOLD signals:           {result['hold_signals']}")
    console.print(f"   Win rate (BUY/total):    {result['win_rate_pct']:.1f}%")
    console.print(f"   Total scans:            {result['total_scans']}")
    if result.get("avg_scan_seconds"):
        console.print(f"   Avg scan time:          {result['avg_scan_seconds']:.2f}s")
    if result.get("last_scan"):
        console.print(f"   Last scan:              {result['last_scan']}")
    console.print(f"   Best strategy:          {result['best_strategy']}")
    console.print(f"   Strategies registered:  {result['strategies_registered']}")
    return 0
