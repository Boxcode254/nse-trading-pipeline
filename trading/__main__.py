"""``python3 -m trading`` — DEPRECATED shim that delegates to the canonical CLI.

WHY THIS EXISTS
===============
This module used to hold a SECOND, contradictory argparse CLI (``run``,
``history``, ``backtest``, ``compare``, ``learn``, ``validators``, ``rank``,
``allocate``) while the installed ``trading`` script ran the modern Typer CLI
in :mod:`trading.cli.main`. Two public entry points describing the same
platform differently was flagged in the 2026-09-15 platform review, so the
legacy implementation is gone: this module now runs the SAME implementation
as ``trading``.

Legacy subcommand names are translated where a canonical equivalent exists.
Names with no equivalent print a migration hint instead of doing something
surprising.

Run ``trading --help`` for the canonical command list.
"""
from __future__ import annotations

import sys
from typing import Optional, Sequence

# Legacy argparse subcommand → canonical CLI argv prefix.
LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    "run": ("morning",),        # full daily brief + scan report
    "rank": ("opportunities",),  # ranked leaderboard
    "allocate": ("target",),     # target-allocation model
}

# Legacy subcommands with no canonical equivalent — say so instead of 404ing.
LEGACY_REMOVED: dict[str, str] = {
    "history": "trading portfolio decisions   (paper trade ledger)",
    "learn": "trading strategies              (strategy registry / status)",
    "validators": "trading config validate    (configuration + filter checks)",
}


def translate_legacy(argv: Sequence[str]) -> list[str]:
    """Map legacy subcommand names onto the canonical CLI argv."""
    args = list(argv)
    if args and args[0] in LEGACY_ALIASES:
        return [*LEGACY_ALIASES[args[0]], *args[1:]]
    return args


def main(argv: Optional[list[str]] = None) -> int:
    """Run the canonical CLI (identical implementation to ``trading``)."""
    from .cli.main import main as cli_main

    raw = list(sys.argv[1:] if argv is None else argv)

    if raw and raw[0] in LEGACY_REMOVED:
        print(
            f"'python3 -m trading {raw[0]}' was removed — "
            f"use: {LEGACY_REMOVED[raw[0]]}",
            file=sys.stderr,
        )
        return 2

    translated = translate_legacy(raw)
    if translated != raw:
        print(
            f"note: 'python3 -m trading {raw[0]}' is deprecated — "
            f"running 'trading {' '.join(translated)}'.",
            file=sys.stderr,
        )

    try:
        return cli_main(translated)
    except SystemExit as exc:  # defensive: some Typer/click paths raise this
        code = exc.code
        return int(code) if isinstance(code, int) else 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
