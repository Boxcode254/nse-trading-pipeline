#!/bin/bash
# Trading Learning Loop — closed-loop cron.
# Runs AFTER the auto-trader (10:30). Ingests new realised outcomes,
# aggregates, and prints a learned signal-gate PROPOSAL.
#
# SAFETY: this job NEVER writes learned_gates.json (no auto-apply). It only
# prints a proposal to the trading Telegram channel. A human approves via
#   .venv/bin/python -m trading.learning_cron --approve <proposal_id>
#
# For no_agent cron delivery: non-empty stdout is delivered verbatim to the
# trading channel; empty stdout stays silent.
set -e
cd ~/.trading || exit 0
source ~/.env 2>/dev/null || true
exec .venv/bin/python -m trading.learning_cron
