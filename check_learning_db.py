import sqlite3
conn = sqlite3.connect('/home/hermes/.trading/learning/learning.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('Tables:', [row[0] for row in cursor.fetchall()])

# Check outcomes in learning db
cursor.execute('SELECT COUNT(*) as count FROM outcomes')
print(f'Total outcomes in learning.db: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM outcomes ORDER BY id DESC LIMIT 10')
for row in cursor.fetchall():
    print(dict(row))

# Check signal_metrics
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signal_metrics'")
print('signal_metrics table exists:', cursor.fetchone() is not None)

cursor.execute('SELECT COUNT(*) as count FROM signal_metrics')
print(f'Signal metrics count: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM signal_metrics')
for row in cursor.fetchall():
    print(dict(row))

# Check recommendations
cursor.execute('SELECT COUNT(*) as count FROM recommendations')
print(f'Recommendations count: {cursor.fetchone()["count"]}')