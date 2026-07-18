import sqlite3
conn = sqlite3.connect('learning/decisions.db')
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(decisions)')
for col in cursor.fetchall():
    print(col)
conn.close()