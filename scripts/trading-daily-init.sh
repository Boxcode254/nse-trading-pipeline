#!/bin/bash
# Merged 6am init — runs the daily signal run (morning brief + learning log).
#
# NOTE: the legacy "rule_updater" auto-tuning step was retired 2026-07-17
# (archived at ~/.trading/archive/rule_updater.py). Its old cron line raised
# ImportError (the live stub no longer exposes main()) and, under `set -e`,
# turned the whole 06:00 daily-init into a spurious daily failure. No live
# code ever consumed its signal_weights, so it was removed — this loses no
# function. (Repo copy; the live cron copy lives at
# ~/.hermes/scripts/trading-daily-init.sh.)
set -e

echo "[06:00] trading-daily-run..."
bash ~/.trading/run-trading.sh

echo "[06:00] Daily init complete"
