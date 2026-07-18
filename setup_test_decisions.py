import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('/home/hermes/.trading/learning/decisions.db')
cursor = conn.cursor()

cursor.execute("DELETE FROM decisions WHERE status = 'OPEN'")
conn.commit()

now = datetime.utcnow()

# SCOM LONG - entry 33.0, stop 31.5, target 36.0 - current price 34.30
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=2)).isoformat(),
    'SCOM', 'momentum', 85, 'LONG',
    33.0, 1000, 31.5, 36.0, 90, 'Strong momentum breakout', 1, 'OPEN'
))

# KCB SHORT - entry 82.0, stop 84.0, target 78.0 - current 79.5
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=5)).isoformat(),
    'KCB', 'mean_reversion', 75, 'SHORT',
    82.0, 500, 84.0, 78.0, 80, 'RSI overbought', 1, 'OPEN'
))

# EQTY LONG - entry 85.0, stop 82.0, target 90.0 - current 87.0
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=10)).isoformat(),
    'EQTY', 'breakout', 80, 'LONG',
    85.0, 100, 82.0, 90.0, 85, 'Breakout above resistance', 1, 'OPEN'
))

# EABL SHORT - entry 275.0, stop 280.0, target 260.0 - current 271.0
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=3)).isoformat(),
    'EABL', 'mean_reversion', 70, 'SHORT',
    275.0, 50, 280.0, 260.0, 75, 'Overbought on daily RSI', 1, 'OPEN'
))

# ABSA LONG - entry 32.0, stop 30.5, target 35.0 - current 33.0
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=8)).isoformat(),
    'ABSA', 'momentum', 78, 'LONG',
    32.0, 200, 30.5, 35.0, 80, 'Momentum continuation', 1, 'OPEN'
))

# SCBK LONG - entry 340.0, stop 330.0, target 365.0 - current 349.75
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=15)).isoformat(),
    'SCBK', 'breakout', 82, 'LONG',
    340.0, 75, 330.0, 365.0, 85, 'Breakout from consolidation', 1, 'OPEN'
))

# Add one expired decision (older than 24h)
cursor.execute("""
INSERT INTO decisions (timestamp, symbol, signal_source, signal_strength, direction,
    entry_price, position_size, stop_loss, take_profit, confidence, reasoning, rule_version, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    (now - timedelta(hours=30)).isoformat(),
    'KCB', 'momentum', 65, 'SHORT',
    80.0, 300, 82.0, 76.0, 70, 'Weak momentum, should expire', 1, 'OPEN'
))

conn.commit()
print("Added test decisions with NSE symbols")

# Verify
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT * FROM decisions WHERE status = "OPEN"').fetchall()
for r in rows:
    print(dict(r))
conn.close()
