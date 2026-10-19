"""``trading stop-cooldown`` — stop-loss re-entry cooldown visibility.

Read-only view of ``~/.trading/portfolio/stop_cooldown.json``: which symbols
are blocked from re-entry after a stop-loss exit, until when, and how many
trading sessions remain. Follows the command-module convention: ``run(**kw)``
calls the service, formats output, returns an exit code (0 ok, 2 on error).
"""

from __future__ import annotations

from typing import Optional


def run(
    as_json: bool = False,
    quiet: bool = False,
    all_entries: bool = False,
    on_date: Optional[str] = None,
) -> int:
    """Print the stop-loss re-entry cooldowns.

    Default shows only ACTIVE cooldowns; ``all_entries=True`` includes expired
    entries for audit. Returns 0 (ok), 1 (active cooldowns exist, for shell
    scriptability ``--json`` still returns 0), or 2 on failure.
    """
    try:
        from trading.stop_cooldown import list_cooldowns

        entries = list_cooldowns(on_date=on_date)
    except Exception as exc:  # noqa: BLE001
        if as_json:
            import json

            print(json.dumps({"error": str(exc)}))
        else:
            print(f"⚠️  Could not read stop-cooldown state: {exc}")
        return 2

    active = {s: e for s, e in entries.items() if e.get("active")}
    shown = entries if all_entries else active

    if as_json:
        import json

        print(json.dumps(shown, indent=2))
        return 0

    if not shown:
        print("No stop-loss re-entry cooldowns"
              + ("" if all_entries else " active") + ".")
        return 0

    if not quiet:
        print("🧊 Stop-loss re-entry cooldowns\n")

    for sym in sorted(shown):
        e = shown[sym]
        # Render the row from the explicit `phase` field (never infer it from
        # `active`): not_started = the exit session itself / pre-window, where
        # ALLOWED is true but the window has not opened yet.
        phase = str(e.get("phase") or "none")
        state = {
            "not_started": "window opens next session",
            "active": "ACTIVE",
            "expired": "expired (re-entry ALLOWED)",
            "none": "no valid cooldown (re-entry ALLOWED)",
        }.get(phase, "no valid cooldown (re-entry ALLOWED)")
        # Never render a fabricated 0.0000 for a price the module does not
        # have: print the recorded price when it is a number, "n/a" otherwise.
        px = e.get("exit_price")
        px_s = (f"{px:.4f}"
                if isinstance(px, (int, float)) and not isinstance(px, bool)
                else "n/a")
        line = (
            f"  {sym:<6s} exited {e.get('exit_date')} @ KES "
            f"{px_s}  {state}"
        )
        if phase == "active":
            line += (
                f" — {e.get('sessions_remaining')} session(s) remaining, "
                f"blocked until {e.get('until')}"
            )
        print(line)
    # Documented contract: rc=1 signals active cooldowns for shell scripting
    # (plain-text only; --json always returns 0 so jq pipelines never see a
    # non-zero exit from merely reporting state).
    return 1 if active else 0
