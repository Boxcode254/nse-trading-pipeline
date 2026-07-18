import sqlite3
conn = sqlite3.connect('/home/hermes/.trading/learning/prices.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = cursor.fetchall()
print('Tables:', [t['name'] for t in tables])
print()
for table in tables:
    tname = table['name']
    cursor.execute(f'PRAGMA table_info({tname})')
    cols = cursor.fetchall()
    print(f'--- {tname} columns ---')
    for col in cols:
        print(f'  {col["name"]} ({col["type"]})')
    print()
    cursor.execute(f'SELECT * FROM {tname} ORDER BY id DESC LIMIT 3')
    rows = cursor.fetchall()
    print(f'--- {tname} data ---')
    for row in rows:
        print(dict(row))
    print()