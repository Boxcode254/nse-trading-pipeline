import sqlite3
conn = sqlite3.connect('/home/hermes/.trading/learning/decisions.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print('=== OPEN DECISIONS ===')
rows = cursor.execute('SELECT id, symbol, direction, entry_price, stop_loss, take_profit, timestamp, status FROM decisions WHERE status = "OPEN"').fetchall()
for r in rows:
    print(dict(r))

print('\n=== CLOSED DECISIONS ===')
rows = cursor.execute('SELECT id, symbol, direction, entry_price, status FROM decisions WHERE status = "CLOSED"').fetchall()
for r in rows:
    print(dict(r))

print('\n=== OUTCOMES ===')
rows = cursor.execute('SELECT * FROM outcomes ORDER BY id DESC LIMIT 10').fetchall()
for r in rows:
    print(dict(r))

conn.close()