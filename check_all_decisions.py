import sqlite3
conn = sqlite3.connect('/home/hermes/.trading/learning/decisions.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
cursor.execute('SELECT * FROM decisions ORDER BY id DESC LIMIT 20')
for row in cursor.fetchall():
    print(dict(row))