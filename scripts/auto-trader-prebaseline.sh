#!/bin/bash
# Pre-run baseline guard for the 10:30 auto-trader-execution cron.
# Captures portfolio state + key file mtimes ~1 minute before the run so the
# post-run monitor (auto-trader-monitor.py) can diff "before vs after".
# Runs silently — the post-run monitor is what reports. (Repo copy; the live
# cron copy lives at ~/.hermes/scripts/auto-trader-prebaseline.sh.)

set -e
cd ~/.trading || exit 1

source ~/.hermes/.env 2>/dev/null || true

.venv/bin/python ~/.hermes/scripts/auto-trader-baseline.py >/dev/null 2>&1 || true

exit 0
