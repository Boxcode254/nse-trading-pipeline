#!/bin/bash
# Outcome Reviewer — recurring monitoring guard.
# Runs the rebuilt reviewer (trading/outcome_reviewer.py) which is SILENT when
# the portfolio is healthy and prints only when there is something to report
# (realised P&L on new sells, fill anomalies, exposure breaches).
#
# For no_agent cron delivery: non-empty stdout is delivered verbatim to the
# trading channel; empty stdout (healthy) stays silent. We just pass the
# module's stdout straight through — do NOT filter it.

set -e
cd ~/.trading || exit 0

# Source env vars for any dependent secrets (kept for parity with siblings).
source ~/.env 2>/dev/null || true

exec .venv/bin/python -m trading.outcome_reviewer
