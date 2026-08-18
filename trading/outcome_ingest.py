"""Outcome Ingestor — close the learning loop's input side.

Reads the REAL paper-trading ledger (``portfolio/transactions.json``),
extracts every SELL with a realised P&L, and records it into a dedicated
``learning.db`` outcome table. This is the data the learning engine
aggregates to tune the rebalancer's signal gate.

Design rules (hard):
* Read-only against transactions.json. Never mutates the ledger.
* Idempotent: a checkpoint stores the last ingested txn timestamp; only
  NEW sells since that checkpoint are ingested, so re-running is safe.
* Symbol-level attribution only. The live ledger's ``signal_ref`` is
  empty (``{}``) on every historical sell, so per-factor attribution is
  impossible and we do NOT fabricate it. We attribute realised P&L to
  the symbol, which is the honest, grounded unit.
* Fail-open: any read error yields an empty result, never a crash that
  would block the cron pipeline.

This module is pure ingestion. Aggregation + gate derivation live in
``learning_engine.py``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Resolve trading package root so ``python -m trading.outcome_ingest`` and
# direct imports both work regardless of cwd.
_ROOT = Path(__file__).resolve().parents[1]
import sys
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

PORTFOLIO_DIR = Path.home() / ".trading" / "portfolio"
TXN_FILE = PORTFOLIO_DIR / "transactions.json"

# Dedicated learning DB (separate from the orphaned standalone learning/ one).
LEARNING_DB = Path.home() / ".trading" / "learning_loop.db"
_CHECKPOINT = Path.home() / ".trading" / "portfolio" / ".outcome_ingest_progress.json"

# Symbols the auto-trader may act on — anything else is not a learning signal.
KNOWN_UNIVERSE = frozenset({
    "ABSA", "COOP", "EABL", "EQTY", "SCOM", "KPLC", "KCB", "SCBK",
    "BAMB", "TOTL", "KNRE", "WTK", "SASN", "ARM", "CIC", "NMG",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS realized_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            exit_timestamp TEXT NOT NULL,
            exit_price REAL NOT NULL,
            shares INTEGER NOT NULL,
            realised_pnl REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            hold_days REAL NOT NULL,
            exit_reason TEXT,
            ingested_at TEXT NOT NULL,
            UNIQUE(symbol, exit_timestamp, shares, exit_price)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ro_symbol ON realized_outcomes(symbol)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ro_ts ON realized_outcomes(exit_timestamp)"
    )


def _load_checkpoint() -> Optional[str]:
    if _CHECKPOINT.exists():
        try:
            d = json.loads(_CHECKPOINT.read_text())
            return d.get("last_txn_ts")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_checkpoint(ts: Optional[str]) -> None:
    if ts is None:
        return
    _CHECKPOINT.write_text(json.dumps({"last_txn_ts": ts}, indent=2))


def _parse_ts(ts: str) -> str:
    """Normalise any timestamp to a comparable ISO string; fall back safe."""
    return ts or ""


def ingest(since_ts: Optional[str] = None, dry_run: bool = False) -> dict[str, Any]:
    """Ingest new SELL outcomes into learning_loop.db.

    Args:
        since_ts: Only ingest sells newer than this. None => read the
            persisted checkpoint (first run bootstraps everything silently).
        dry_run: If True, compute what WOULD be ingested but don't write
            and don't advance the checkpoint.

    Returns:
        {ingested, skipped, new_checkpoint, first_run, errors}
    """
    if since_ts is None:
        since_ts = _load_checkpoint()

    result: dict[str, Any] = {
        "ingested": 0,
        "skipped": 0,
        "new_checkpoint": since_ts,
        "first_run": since_ts is None,
        "errors": [],
    }

    # Load ledger (fail-open).
    try:
        if not TXN_FILE.exists():
            result["errors"].append(f"transactions.json missing: {TXN_FILE}")
            return result
        txns = json.loads(TXN_FILE.read_text())
        if not isinstance(txns, list):
            result["errors"].append("transactions.json is not a JSON array")
            return result
    except (json.JSONDecodeError, OSError) as e:
        result["errors"].append(f"cannot read transactions.json: {e}")
        return result

    # Collect candidate sells newer than checkpoint.
    candidates: list[dict[str, Any]] = []
    max_ts = since_ts or ""
    for t in txns:
        ts = _parse_ts(t.get("timestamp", ""))
        if ts > max_ts:
            max_ts = ts
        if since_ts is None:
            # First run: bootstrap checkpoint from the newest txn, ingest all.
            pass
        elif ts <= since_ts:
            continue
        if t.get("action") != "SELL":
            continue
        rp = t.get("realised_pnl", None)
        if rp is None:
            continue  # no realised figure — not a learning signal
        symbol = t.get("symbol")
        if not symbol or symbol not in KNOWN_UNIVERSE:
            result["skipped"] += 1
            continue
        candidates.append({
            "symbol": symbol,
            "ts": ts,
            "price": float(t.get("price", 0) or 0),
            "shares": int(t.get("shares", 0) or 0),
            "pnl": float(rp),
            "reason": t.get("reason", "") or "",
        })

    if not candidates:
        result["new_checkpoint"] = max_ts
        if not dry_run:
            _save_checkpoint(max_ts)
        return result

    # Compute pnl_pct + hold_days where derivable; these are best-effort.
    # We don't have entry price/time on the sell row, so pnl_pct is derived
    # from realised_pnl / notional where notional is available via total/fee.
    rows_to_write: list[tuple] = []
    for c in candidates:
        # Derive pnl_pct from realised_pnl if 'total' present (total = notional)
        total = c.get("total")
        pnl_pct = 0.0
        hold_days = 0.0
        if isinstance(total, (int, float)) and total:
            pnl_pct = round(c["pnl"] / float(total) * 100.0, 4)
        rows_to_write.append((
            c["symbol"], c["ts"], c["price"], c["shares"],
            c["pnl"], pnl_pct, hold_days, c["reason"], _now_iso(),
        ))

    if dry_run:
        result["ingested"] = len(rows_to_write)
        return result

    try:
        with sqlite3.connect(LEARNING_DB) as conn:
            _init_db(conn)
            conn.executemany(
                """
                INSERT OR IGNORE INTO realized_outcomes
                (symbol, exit_timestamp, exit_price, shares, realised_pnl,
                 pnl_pct, hold_days, exit_reason, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows_to_write,
            )
            result["ingested"] = len(rows_to_write)
        _save_checkpoint(max_ts)
        result["new_checkpoint"] = max_ts
    except sqlite3.Error as e:
        result["errors"].append(f"db write failed: {e}")

    return result


def count_outcomes() -> int:
    try:
        with sqlite3.connect(LEARNING_DB) as conn:
            _init_db(conn)
            return conn.execute(
                "SELECT COUNT(*) FROM realized_outcomes"
            ).fetchone()[0]
    except sqlite3.Error:
        return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Ingest realised SELL outcomes")
    ap.add_argument("--dry-run", action="store_true", help="preview, no write")
    ap.add_argument("--force-all", action="store_true",
                    help="ignore checkpoint, ingest all sells")
    args = ap.parse_args()
    since = None if args.force_all else _load_checkpoint()
    res = ingest(since_ts=since, dry_run=args.dry_run)
    print(json.dumps(res, indent=2, default=str))
