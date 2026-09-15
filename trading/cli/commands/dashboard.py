"""``trading dashboard`` — canonical CURRENT portfolio status (read-only).

This command used to render a retired ("archived 2026-07-17") paper-engine
dashboard and print archived numbers as if they were live. The platform
review flagged that as a trust defect: the public surface must not present
archived data as current.

It now emits the canonical read-only summary sourced from
``portfolio/mtm_state.json`` (see :mod:`trading.services.portfolio_status`).
Nothing here writes: no snapshots, no state files, no caches.

Usage::

    trading dashboard                        Current status, text
    trading dashboard --json                 Current status, JSON
    trading dashboard --html                 Current status, HTML
    trading dashboard --output status.html   Write HTML to FILE

The former ``dashboard serve`` web server (which regenerated the archived
HTML on every request) has been removed with the archived surface; use
``--output`` plus any static file server instead.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from rich.console import Console

from .. import output
from ...services import portfolio_status

console = Console()


def run(
    html: bool = False,
    no_telegram: bool = False,
    output_path: Optional[str] = None,
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Print the canonical current portfolio status.

    ``no_telegram`` is accepted for backwards compatibility with callers
    that passed it; this surface never sends anything anywhere.
    """
    status = portfolio_status.current_status()

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if html:
            out.write_text(portfolio_status.format_status_html(status))
        elif as_json:
            out.write_text(json.dumps(status, indent=2, default=str))
        else:
            out.write_text(portfolio_status.format_status(status))
        if not quiet:
            console.print(f"[green]Status written to {out}[/]")
        return 0

    if html:
        print(portfolio_status.format_status_html(status))
        return 0

    if as_json:
        print(output.json_dumps(status))
        return 0

    if not quiet:
        print(portfolio_status.format_status(status))
    return 0
