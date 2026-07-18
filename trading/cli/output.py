"""Output helpers for the CLI.

Centralises JSON serialization and rich-text rendering so every
command produces consistent output.
"""
from __future__ import annotations

import io
import json
from typing import Any, Sequence

from rich.console import Console
from rich.table import Table


def json_dumps(obj: Any) -> str:
    """Serialise *obj* as compact, deterministic JSON (sorted keys)."""
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def make_console(quiet: bool = False) -> Console:
    """Build a Rich console.

    ``quiet=True`` returns a console with no terminal width
    autodetection, which keeps output readable in tests and CI logs.
    """
    return Console(file=io.StringIO(), quiet=quiet, force_terminal=False) if quiet else Console()


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    title: str | None = None,
) -> str:
    """Render a tabular output to a plain-text string.

    Output is a pipe-separated table with column headers. Useful for
    ``trading opportunities``, ``trading strategies``, etc.
    """
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    table = Table(title=title, show_header=True, header_style="bold")
    for h in headers:
        table.add_column(h)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)
    return buf.getvalue()


def render_panel(title: str, body: str, style: str = "bold") -> str:
    """Render a single panel (used by ``trading morning`` etc)."""
    from rich.panel import Panel
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    console.print(Panel(body, title=title, border_style=style))
    return buf.getvalue()
