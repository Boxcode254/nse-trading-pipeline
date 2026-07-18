"""``trading opportunities`` — ranked list with narrative context.

Combines the structured ranked table with the Investment Advisor's
plain-language enrichment so the user sees both the numbers and
the meaning in one place.
"""
from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.table import Table

from .. import output
from ... import config
from ...services import advisor, ranking as ranking_svc


def run(quiet: bool = False, as_json: bool = False, verbose: bool = False) -> int:
    """Show the ranked opportunities table + narrative enrichment."""
    result = ranking_svc.build()
    if as_json:
        print(output.json_dumps(result))
        return 0

    ranked = result.get("ranked", [])

    if quiet:
        for r in ranked:
            print(
                f"#{r.get('rank', 0):<2d}  {r['symbol']:<8s}  "
                f"{r['score']:5.1f}  {r['recommendation']:<18s}  "
                f"{r.get('holding_period', '')}"
            )
        return 0

    console = Console()
    table = Table(
        title="🌟 Market Opportunities",
        show_header=True,
        header_style="bold",
    )
    table.add_column("#", justify="right")
    table.add_column("Asset", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Recommendation")
    table.add_column("Holding")

    for r in ranked:
        rec = r["recommendation"]
        emoji = {
            "Strong Accumulate": "🟢",
            "Accumulate": "🟩",
            "Hold": "🟡",
            "Reduce": "🟠",
            "Avoid": "🔴",
        }.get(rec, "⚪")
        table.add_row(
            str(r.get("rank", "")),
            r["symbol"],
            f"{r['score']:.1f}",
            f"{emoji} {rec}",
            r.get("holding_period", ""),
        )
    console.print(table)

    # ── Narrative enrichment from the Investment Advisor ─────────
    console.print("\n[bold]In plain English:[/]\n")
    opps_block = advisor.enrich_opportunities(ranked, top_n=3)
    console.print(opps_block)
    console.print("")

    warns_block = advisor.enrich_warnings(ranked, top_n=3)
    console.print(warns_block)
    return 0
