"""``trading doctor`` — full health check."""
from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from .. import output
from ...services import health


def run(quiet: bool = False, as_json: bool = False) -> int:
    """Run the doctor and exit 0/1/2 according to status."""
    result = health.doctor()

    if as_json:
        print(output.json_dumps(result))
        return 0 if result["status"] != "failure" else 2

    console = Console()
    status = result["status"]
    if status == "healthy":
        console.print(f"[bold green]✅ HEALTHY[/]  score: {result['health_score']}/100")
    elif status == "warning":
        console.print(f"[bold yellow]⚠️  WARNING[/]  score: {result['health_score']}/100")
    else:
        console.print(f"[bold red]❌ FAILURE[/]  score: {result['health_score']}/100")

    table = Table(title="Health Checks", show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Message")
    for c in result["checks"]:
        mark = "✓" if c["ok"] else "✗"
        style = "green" if c["ok"] else "red"
        table.add_row(c["name"], f"[{style}]{mark}[/]", c.get("message", ""))
    console.print(table)

    if result["warnings"]:
        console.print("\n[bold yellow]Warnings[/]")
        for w in result["warnings"]:
            console.print(f"  • {w}")
    if result["errors"]:
        console.print("\n[bold red]Errors[/]")
        for e in result["errors"]:
            console.print(f"  • {e}")
    if result["recommendations"]:
        console.print("\n[bold cyan]Recommendations[/]")
        for r in result["recommendations"]:
            console.print(f"  • {r}")

    if status == "healthy":
        return 0
    if status == "warning":
        return 1
    return 2
