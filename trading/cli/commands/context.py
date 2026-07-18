"""``trading context [SYMBOL]`` — show what's driving an asset (or the market).

Examples
--------

    # What's driving SCOM right now?
    trading context SCOM

    # Show top macro events this week
    trading context --market

    # JSON output (for dashboards / bots)
    trading context KCB --json
"""
from __future__ import annotations

from typing import Optional

from .. import output
from ...services.market_intel import calendar, context, news, sector


def run(
    symbol: Optional[str] = None,
    *,
    market: bool = False,
    as_json: bool = False,
) -> int:
    """Show the market context for one symbol, or top macro events.

    Parameters
    ----------
    symbol : str, optional
        The asset to show context for. Required unless ``--market``
        is passed.
    market : bool, default ``False``
        When True, show top macro events for the week instead of
        per-symbol context.
    as_json : bool, default ``False``
        Emit JSON instead of formatted text.
    """
    if market:
        return _run_market(as_json=as_json)
    if not symbol:
        print("error: provide a SYMBOL or pass --market", file=__import__("sys").stderr)
        return 2

    items = context.assemble(symbol, max_items=3)
    if as_json:
        print(output.json_dumps({"symbol": symbol, "items": items}))
        return 0

    print(context.format_block(symbol, items))
    return 0


def _run_market(*, as_json: bool) -> int:
    """Show the top macro events for the next 30 days."""
    events = calendar.upcoming(within_days=30)
    # Sort: high impact first, then medium, then low; then by date.
    impact_order = {"high": 0, "medium": 1, "low": 2}
    events = sorted(
        events,
        key=lambda e: (impact_order.get(e.get("impact", "low"), 9),
                       e.get("date", "")),
    )
    events = events[:5]
    if as_json:
        print(output.json_dumps({"events": events}))
        return 0

    if not events:
        print("No notable macro events in the next 30 days.")
        return 0

    print("Top macro events (next 30 days):")
    for ev in events:
        print(f"  • {calendar.format_event(ev)}")
    return 0
