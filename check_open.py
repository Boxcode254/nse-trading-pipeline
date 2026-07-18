import sqlite3
conn = sqlite3.connect('/home/hermes/.trading/learning/decisions.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
cursor.execute('SELECT * FROM decisions WHERE status = "OPEN"')
rows = cursor.fetchall()
print(f'Open decisions: {len(rows)}')
for row in rows:
    print(dict(row))