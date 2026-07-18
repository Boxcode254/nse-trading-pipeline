"""``trading strategies`` — list registered strategies."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .. import output
from ...services import strategies as strat_svc


def run(quiet: bool = False, as_json: bool = False, verbose: bool = False, output_path: str | None = None) -> int:
    """List every registered strategy with its status."""
    items = strat_svc.list_registered()
    if as_json:
        print(output.json_dumps({"strategies": items}))
        return 0

    if quiet:
        for s in items:
            print(f"{s['key']}  {s['status']:<14s}  {s['name']}")
        return 0

    console = Console()
    table = Table(title="📚 Registered Strategies", show_header=True, header_style="bold")
    table.add_column("Key", style="cyan")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Description")
    for s in items:
        status_style = "green" if s["status"] == "Benchmark" else "yellow"
        table.add_row(
            s["key"],
            s["name"],
            f"[{status_style}]{s['status']}[/]",
            s["description"],
        )
    console.print(table)
    return 0
