import sqlite3
conn = sqlite3.connect('/home/hermes/.trading/learning/prices.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
cursor.execute('SELECT * FROM daily_closes WHERE symbol IN ("SCOM", "KCB", "EQTY", "EABL", "ABSA", "SCBK") ORDER BY date DESC')
rows = cursor.fetchall()
for row in rows:
    print(dict(row))