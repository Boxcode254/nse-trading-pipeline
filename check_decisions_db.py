import sqlite3
conn = sqlite3.connect('/home/hermes/.trading/learning/decisions.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check decisions table
cursor.execute('SELECT COUNT(*) as count FROM decisions')
print(f'Total decisions: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM decisions WHERE status = "OPEN"')
for row in cursor.fetchall():
    print(dict(row))

# Check outcomes table in decisions.db
cursor.execute('SELECT COUNT(*) as count FROM outcomes')
print(f'Total outcomes in decisions.db: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM outcomes ORDER BY id DESC LIMIT 10')
for row in cursor.fetchall():
    print(dict(row))

# Check signal metrics in decisions.db
cursor.execute('SELECT COUNT(*) as count FROM signal_metrics')
print(f'Signal metrics count: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM signal_metrics')
for row in cursor.fetchall():
    print(dict(row))

# Schema of decisions table
cursor.execute('PRAGMA table_info(decisions)')
print('\nDecisions schema:')
for row in cursor.fetchall():
    print(dict(row))

# Schema of outcomes table
cursor.execute('PRAGMA table_info(outcomes)')
print('\nOutcomes schema:')
for row in cursor.fetchall():
    print(dict(row))

# Check for signal_metrics table
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signal_metrics'")
print('\nsignal_metrics table exists:', cursor.fetchone() is not None)