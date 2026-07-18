import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('/home/hermes/.trading/learning/decisions.db')
cursor = conn.cursor()

now = datetime.utcnow()

# SCOM LONG: entry 33.0, stop 35.0 (above current 34.3), target 36.0
# Current 34.3 <= 35.0 -> STOP HIT
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=1)).isoformat(),
    'SCOM', 'test_stop', 85, 'LONG',
    33.0, 100, 35.0, 36.0, 90, 'Test stop loss hit', 1, 'OPEN'
))

# ABSA LONG: entry 31.0, stop 30.0, target 32.5 (below current 33.0)
# Current 33.0 >= 32.5 -> TARGET HIT
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=1)).isoformat(),
    'ABSA', 'test_target', 75, 'LONG',
    31.0, 200, 30.0, 32.5, 80, 'Test take profit hit', 1, 'OPEN'
))

# KCB SHORT: entry 80.0, stop 79.0 (below current 79.5), target 77.0
# For SHORT, stop hit when price >= stop. Current 79.5 >= 79.0 -> STOP HIT
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=1)).isoformat(),
    'KCB', 'test_stop_short', 70, 'SHORT',
    80.0, 300, 79.0, 77.0, 85, 'Test stop loss hit for SHORT', 1, 'OPEN'
))

# EQTY LONG: entry 86.0, target 86.5 (current 87.0), stop 84.0
# Current 87.0 >= 86.5 -> TARGET HIT
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=1)).isoformat(),
    'EQTY', 'test_target_long', 80, 'LONG',
    86.0, 100, 84.0, 86.5, 85, 'Test take profit hit for LONG', 1, 'OPEN'
))

conn.commit()
print("Added test decisions for stop/target hits")

# Verify
conn.row_factory = sqlite3.Row
rows = cursor.execute('SELECT id, symbol, direction, entry_price, stop_loss, take_profit, timestamp FROM decisions WHERE status = "OPEN"').fetchall()
for r in rows:
    print(dict(r))

conn.close()
