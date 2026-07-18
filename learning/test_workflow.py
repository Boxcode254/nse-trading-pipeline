#!/usr/bin/env python3
"""Test full workflow via import."""

import sys
sys.path.insert(0, '/home/hermes/.trading')

from trading.learning import get_conn, add_decision, add_rule_version, get_open_decisions
from datetime import datetime

# Test full workflow
conn = get_conn()
print('get_conn() works:', conn is not None)
conn.close()

decision_id = add_decision(
    timestamp=datetime.now().isoformat(),
    symbol='TCS',
    signal_source='mean_reversion',
    signal_strength=85,
    direction='SHORT',
    entry_price=3200.0,
    position_size=50,
    stop_loss=3300.0,
    take_profit=3000.0,
    confidence=90,
    reasoning='RSI overbought on 4h chart',
    rule_version=1
)
print(f'Added decision: {decision_id}')

rv = add_rule_version(
    description='Mean reversion v2 - adjusted thresholds',
    params_json='{"rsi_oversold": 30, "rsi_overbought": 70, "bb_period": 20}',
    parent_version=1
)
print(f'Added rule version: {rv}')

open_decisions = get_open_decisions()
print(f'Open decisions: {len(open_decisions)}')

print('All functions work!')