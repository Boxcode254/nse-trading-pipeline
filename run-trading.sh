#!/bin/bash
# Trading agent — daily signal run with learning integration
# stdout is delivered verbatim to Telegram via Hermes cron (no_agent=True)
# Uses the new Typer CLI for morning brief + learning log

cd ~/.trading || exit 1

# Run morning brief which now integrates learning
# Refresh MTM prices in state.json (use the trading venv for a single
# consistent interpreter across the whole pipeline)
# Points at the NEW price_source-wired copy — the OLD ~/.hermes/scripts one
# (no AXYS awareness, retired 2026-08-26) caused the two-copies drift bug.
.venv/bin/python scripts/refresh-mtm.py

exec .venv/bin/python -m trading.cli.main morning --telegram --save 2>&1

# After morning completes, run monthly report (first of month)
# This is handled separately via cron on day 1 of each month