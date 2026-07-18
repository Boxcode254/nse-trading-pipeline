"""``trading allocations`` — placeholder."""
from __future__ import annotations

from .. import output


def run(quiet: bool = False, as_json: bool = False) -> int:
    """Show target allocations (placeholder)."""
    payload = {
        "status": "coming_soon",
        "feature": "allocations",
        "message": (
            "Target allocations are on the Phase 4 roadmap. "
            "Future interface: target-allocation model + drift-based "
            "trade generation with paper-trading execution."
        ),
        "planned_subcommands": ["plan", "execute", "drift"],
    }
    if quiet or as_json:
        print(output.json_dumps(payload))
        return 0
    print(f"📊  Allocations — {payload['status'].upper()}")
    print(f"   {payload['message']}")
    return 0