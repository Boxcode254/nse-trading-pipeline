import sqlite3
conn = sqlite3.connect('learning/learning.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check outcomes table
cursor.execute('SELECT COUNT(*) as count FROM outcomes')
print(f'Total outcomes: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM outcomes ORDER BY id DESC LIMIT 10')
for row in cursor.fetchall():
    print(dict(row))

# Check signal metrics
cursor.execute('SELECT COUNT(*) as count FROM signal_metrics')
print(f'Signal metrics count: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM signal_metrics')
for row in cursor.fetchall():
    print(dict(row))

# Check decisions
cursor.execute('SELECT COUNT(*) as count FROM decisions')
print(f'Total decisions: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM decisions WHERE status = "OPEN"')
for row in cursor.fetchall():
    print(dict(row))