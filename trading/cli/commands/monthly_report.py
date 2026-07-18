"""Monthly Report CLI command."""

import typer
from typing import Optional

from learning.monthly_report import main as generate_report


def run(
    months: int = typer.Option(1, "--months", "-m", help="Months of history to include"),
    telegram: bool = typer.Option(False, "--telegram", "-t", help="Send to Telegram"),
    save: bool = typer.Option(False, "--save", "-s", help="Save to ~/.trading/logs/"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Custom output file"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> int:
    """Generate monthly performance report."""
    return generate_report(
        months=months,
        telegram=telegram,
        save=save,
        output=output,
        quiet=quiet,
        as_json=as_json,
    )