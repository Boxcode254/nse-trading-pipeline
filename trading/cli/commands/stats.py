"""``trading stats`` — platform statistics."""
from __future__ import annotations

from rich.console import Console

from .. import output
from ...services import stats as stats_svc


def run(quiet: bool = False, as_json: bool = False, verbose: bool = False, output_path: str | None = None) -> int:
    """Show platform stats: signal counts, scan counts, outcome performance."""
    result = stats_svc.build()
    perf = result.get("outcome_performance", {})
    if as_json:
        print(output.json_dumps(result))
        return 0
    if quiet:
        print(
            f"signals={result['total_signals']} scans={result['total_scans']} "
            f"buy={result['buy_signals']} sell={result['sell_signals']} "
            f"buy_share={result['buy_signal_share_pct']:.1f}% "
            f"outcomes={perf.get('evaluated_outcomes', 0)} "
            f"sample={perf.get('sample_status', 'unknown')}"
        )
        return 0
    console = Console()
    console.print("\n📊 Platform Stats")
    console.print(f"   Total signals logged:    {result['total_signals']}")
    console.print(f"   BUY signals:            {result['buy_signals']}")
    console.print(f"   SELL signals:           {result['sell_signals']}")
    console.print(f"   HOLD signals:           {result['hold_signals']}")
    console.print(
        f"   BUY share of signals:    {result['buy_signal_share_pct']:.1f}%  (signal mix, not a win rate)"
    )
    console.print(
        f"   BUY share of BUY+SELL:   {result['buy_share_of_actionable_pct']:.1f}%"
    )
    console.print("   — Outcome performance (closed outcomes only) —")
    console.print(f"   Evaluated outcomes:     {perf.get('evaluated_outcomes', 0)}")
    console.print(
        f"   Evaluated coverage:      {perf.get('coverage_pct', 0.0):.2f}% of "
        f"{perf.get('recommendations_total', 0)} recommendations "
        f"({perf.get('eligible_sample', 0)} actionable eligible, "
        f"{perf.get('unresolved_sample', 0)} unresolved)"
    )
    if perf.get("win_rate_pct") is None:
        console.print(f"   Outcome win rate:        n/a — {perf.get('caveat', 'unavailable')}")
    else:
        console.print(
            f"   Outcome win rate:        {perf['win_rate_pct']:.1f}%  "
            f"({perf.get('numerator', 0)}/{perf.get('denominator', 0)})"
        )
    console.print(f"   Total scans:            {result['total_scans']}")
    if result.get("avg_scan_seconds"):
        console.print(f"   Avg scan time:          {result['avg_scan_seconds']:.2f}s")
    if result.get("last_scan"):
        console.print(f"   Last scan:              {result['last_scan']}")
    console.print(f"   Best strategy:          {result['best_strategy']}")
    console.print(f"   Strategies registered:  {result['strategies_registered']}")
    return 0
