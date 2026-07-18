#!/usr/bin/env python3
"""Test script to verify db.py works."""

import sys
sys.path.insert(0, '/home/hermes/.trading/learning')

from db import init_db, get_conn, add_decision, add_outcome, add_rule_version

print("Testing init_db()...")
init_db()
print("init_db() succeeded")

print("Testing get_conn()...")
conn = get_conn()
print("get_conn() succeeded:", conn)
conn.close()

print("Testing add_decision insertion...")
from datetime import datetime
decision_id = add_decision(
    timestamp=datetime.now().isoformat(),
    symbol="RELIANCE",
    signal_source="momentum",
    signal_strength=75,
    direction="LONG",
    entry_price=2500.0,
    position_size=100,
    stop_loss=2400.0,
    take_profit=2700.0,
    confidence=80,
    reasoning="Strong momentum signal on daily chart",
    rule_version=1
)
print(f"Added decision: {decision_id}")

print("Testing add_rule_version...")
rv = add_rule_version(
    description="Initial momentum strategy",
    params_json='{"rsi_period": 14, "macd_fast": 12, "macd_slow": 26}',
    parent_version=None
)
print(f"Added rule version: {rv}")

print("All tests passed!")