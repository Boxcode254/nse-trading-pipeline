"""``trading allocations`` — canonical alias for ``trading target``.

This used to be a ``status=coming_soon`` placeholder even though target
allocation has been implemented and is what the auto-trader executes. Two
public surfaces describing the same thing differently is exactly the kind
of contradiction the platform review flagged, so this command now simply
delegates to :mod:`trading.cli.commands.target_allocation` (the
implemented target-allocation model) instead of advertising a roadmap.
"""
from __future__ import annotations

from . import target_allocation as target_cmd


def run(quiet: bool = False, as_json: bool = False, **kwargs) -> int:
    """Alias for ``trading target`` (no rebalance plan, no verify)."""
    return target_cmd.run(
        quiet=quiet,
        as_json=as_json,
        show_rebalance=bool(kwargs.get("show_rebalance", False)),
        dry_run=bool(kwargs.get("dry_run", True)),
        verify=bool(kwargs.get("verify", False)),
    )
