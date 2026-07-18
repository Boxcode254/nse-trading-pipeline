import sqlite3
conn = sqlite3.connect('learning/decisions.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Open positions
cursor.execute('SELECT * FROM decisions WHERE status = "OPEN"')
open_positions = cursor.fetchall()
print('=== OPEN POSITIONS ===')
for p in open_positions:
    print(f'  ID: {p["id"]}, Symbol: {p["symbol"]}, Side: {p["side"]}, Entry: {p["entry_price"]}, Stop: {p["stop_price"]}, Target: {p["target_price"]}, Expires: {p["expires_at"]}')

# Closed positions (recent)
cursor.execute('SELECT * FROM decisions WHERE status != "OPEN" ORDER BY id DESC LIMIT 10')
closed_positions = cursor.fetchall()
print('\n=== RECENT CLOSED POSITIONS ===')
for p in closed_positions:
    print(f'  ID: {p["id"]}, Symbol: {p["symbol"]}, Status: {p["status"]}, Exit: {p["exit_price"]}, PnL: {p["pnl_absolute"]}')

conn.close()