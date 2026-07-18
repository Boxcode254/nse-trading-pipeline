import sqlite3
conn = sqlite3.connect('/home/hermes/.trading/learning/prices.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute('SELECT COUNT(*) as count FROM daily_closes')
print(f'Total daily_closes: {cursor.fetchone()["count"]}')

cursor.execute('SELECT * FROM daily_closes ORDER BY date DESC LIMIT 20')
for row in cursor.fetchall():
    print(dict(row))

cursor.execute('SELECT * FROM recommendation_outcomes ORDER BY id DESC LIMIT 10')
for row in cursor.fetchall():
    print(dict(row))