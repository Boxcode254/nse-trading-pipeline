#!/bin/bash
# Hourly Outcome Reviewer — runs the Python module directly.
# Only outputs text when positions are closed (silent otherwise).
# For no_agent cron delivery: non-empty stdout = delivered, empty = silent.

set -e
cd ~/.trading || exit 0

# Source env vars for Telegram token
source ~/.env 2>/dev/null || true

# Run the reviewer and capture output
output=$(.venv/bin/python -m trading.outcome_reviewer 2>&1) || true

# Check if any positions were closed
if echo "$output" | grep -q "closed=[1-9]"; then
    # Positions were closed — extract the relevant parts for delivery
    echo "$output" | grep -E "(Closed:|closed=|pnl=|Review complete)" || echo "$output"
elif echo "$output" | grep -q "Fatal error"; then
    # Error occurred — report it
    echo "⚠️ Outcome reviewer error:"
    echo "$output" | tail -5
fi

# If no positions closed and no errors, output nothing (silent)
