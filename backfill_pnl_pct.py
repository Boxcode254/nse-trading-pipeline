"""One-shot backfill: recompute pnl_pct (and hold_days) for existing
realized_outcomes rows from the ledger's total. Mirrors ingest derivation."""
import json
import sqlite3
from pathlib import Path

ROOT = Path.home() / ".trading"
TXN_FILE = ROOT / "portfolio" / "transactions.json"
DB = ROOT / "learning_loop.db"

txns = json.loads(TXN_FILE.read_text())
# index sells by (symbol, timestamp) — matches ingest's unique key
sells = {}
for t in txns:
    if t.get("action") == "SELL" and t.get("realised_pnl") is not None:
        sells[(t.get("symbol"), t.get("timestamp"))] = t

con = sqlite3.connect(DB)
rows = con.execute("SELECT id, symbol, exit_timestamp, realised_pnl FROM realized_outcomes").fetchall()
updated = skipped = 0
for rid, sym, ts, pnl in rows:
    txn = sells.get((sym, ts))
    if txn is None:
        skipped += 1
        continue
    total = txn.get("total")
    if not isinstance(total, (int, float)) or not total:
        skipped += 1
        continue
    pnl_pct = round(float(pnl) / float(total) * 100.0, 4)
    con.execute("UPDATE realized_outcomes SET pnl_pct=? WHERE id=?", (pnl_pct, rid))
    updated += 1
con.commit()

# verify
check = con.execute("SELECT COUNT(*), SUM(CASE WHEN pnl_pct != 0 THEN 1 ELSE 0 END) FROM realized_outcomes").fetchone()
print(f"backfilled: {updated}, skipped: {skipped}, store now: {check[0]} rows, non-zero pnl_pct: {check[1]}")
con.close()
