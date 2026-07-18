#!/bin/bash
# Auto-trader — runs after NSE market close
# stdout is delivered verbatim as the end-of-day report

cd ~/.trading || exit 1
exec .venv/bin/python -m trading.auto_trader 2>&1
