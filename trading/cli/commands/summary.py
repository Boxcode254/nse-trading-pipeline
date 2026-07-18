"""``trading summary`` — concise market overview.

A short version of ``trading morning`` focused on the actionable
bits: top opportunities, market opportunity score, headline signals.
"""
from __future__ import annotations

import json

from rich.console import Console

from .. import output
from ...services import ranking


def run(quiet: bool = False, as_json: bool = False, verbose: bool = False) -> int:
    """Print a concise market summary."""
    result = ranking.build()
    ranked = result.get("ranked", [])
    if as_json:
        print(output.json_dumps({
            "top": ranked[:5],
            "market_opportunity_score": _market_score(ranked),
        }))
        return 0
    if quiet:
        for r in ranked[:5]:
            print(f"#{r.get('rank', 0):<2d}  {r['symbol']:<8s}  {r['score']:5.1f}  {r['recommendation']}")
        return 0
    console = Console()
    score = _market_score(ranked)
    console.print(f"\n🌍 Market Opportunity Score: [bold]{score}/100[/]")
    console.print(f"   Assets ranked: {len(ranked)}")
    if ranked:
        console.print(f"   Top pick:      {ranked[0]['symbol']}  ({ranked[0]['score']:.1f}, {ranked[0]['recommendation']})")
        console.print(f"   Bottom:        {ranked[-1]['symbol']}  ({ranked[-1]['score']:.1f}, {ranked[-1]['recommendation']})")
    console.print()
    console.print("Top 5:")
    for r in ranked[:5]:
        console.print(f"  #{r.get('rank', 0):<2d}  {r['symbol']:<8s}  {r['score']:5.1f}  {r['recommendation']}")
    return 0


def _market_score(ranked: list[dict]) -> float:
    """A simple market opportunity score: mean of all ranked scores."""
    if not ranked:
        return 0.0
    return round(sum(r["score"] for r in ranked) / len(ranked), 1)
