"""``trading dashboard`` — supervision dashboard reports and server.

Usage::

    trading dashboard                        Text report to stdout
    trading dashboard --html                 Print HTML report
    trading dashboard --output report.html   Write HTML to file
    trading dashboard --no-telegram          Text report, skip Telegram
    trading dashboard serve [--port 9210]    Live web server
"""
from __future__ import annotations

import json
import logging
import os
import sys
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from io import StringIO
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .. import output
from ...dashboard import Dashboard, create_dashboard

log = logging.getLogger(__name__)
console = Console()


def run(
    html: bool = False,
    no_telegram: bool = False,
    output_path: Optional[str] = None,
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Generate and display the supervision dashboard report."""
    try:
        dashboard = create_dashboard()
    except Exception as e:
        if not quiet:
            console.print(f"[red]Error initialising dashboard: {e}[/]")
        return 1

    if html or output_path:
        # HTML mode
        try:
            content = dashboard.generate_html_report()
        except Exception as e:
            if not quiet:
                console.print(f"[red]Error generating HTML report: {e}[/]")
            return 1

        if output_path:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content)
            if not quiet:
                console.print(f"[green]HTML report written to {out}[/]")
            return 0

        # Print HTML to stdout
        if as_json:
            print(json.dumps({"html": content}))
        else:
            print(content)
        return 0

    # Text mode
    try:
        report = dashboard.run_report(send_telegram=not no_telegram)
    except Exception as e:
        if not quiet:
            console.print(f"[red]Error generating report: {e}[/]")
        return 1

    if as_json:
        print(json.dumps({"report": report}))
    elif not quiet:
        print(report)

    return 0


# ── Serve subcommand ──────────────────────────────────────────────────────

_REPORT_CACHE: str | None = None


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves the live-generated HTML dashboard."""

    def do_GET(self) -> None:
        global _REPORT_CACHE
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        try:
            content = create_dashboard().generate_html_report()
            _REPORT_CACHE = content
        except Exception:
            if _REPORT_CACHE is not None:
                content = _REPORT_CACHE
            else:
                content = "<html><body><h1>Dashboard Error</h1><p>Failed to generate report.</p></body></html>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug(fmt, *args)


def serve(port: int = 9210, quiet: bool = False) -> int:
    """Start a live web server serving the dashboard on each request."""
    host = "0.0.0.0"

    try:
        server = HTTPServer((host, port), DashboardHandler)
    except OSError as e:
        if not quiet:
            console.print(f"[red]Failed to bind to {host}:{port} — {e}[/]")
        return 1

    url = f"http://{host}:{port}/"
    if not quiet:
        console.print(f"[green]Dashboard server started on {url}[/]")
        console.print("[dim]Regenerates HTML on each request. Ctrl+C to stop.[/]")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            console.print("\n[yellow]Shutting down...[/]")
        server.shutdown()
        return 0

    return 0
