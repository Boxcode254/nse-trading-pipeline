import sqlite3
conn = sqlite3.connect('learning/decisions.db')
cursor = conn.cursor()
cursor.execute('SELECT * FROM decisions WHERE status = "CLOSED" ORDER BY created_at DESC LIMIT 10')
for row in cursor.fetchall():
    print(row)
conn.close()