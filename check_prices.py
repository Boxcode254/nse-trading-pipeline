import sqlite3
conn = sqlite3.connect('prices.db')
conn.row_factory = sqlite3.Row
tables = conn.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()
print([t['name'] for t in tables])