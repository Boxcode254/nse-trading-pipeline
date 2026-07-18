"""Decision Journal Database - SQLite operations for paper trading decision tracking."""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from contextlib import contextmanager

# Database paths
DECISIONS_DB_PATH = Path.home() / ".trading" / "learning" / "decisions.db"
SCHEMA_PATH = Path.home() / ".trading" / "learning" / "schema.sql"


def get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a database connection with row factory set."""
    path = db_path or DECISIONS_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_connection(db_path: Optional[Path] = None):
    """Context manager for database connections with auto-commit/rollback."""
    conn = get_conn(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize the decision journal database with schema."""
    path = db_path or DECISIONS_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(path) as conn:
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
    print(f"Initialized database at {path}")


# Convenience functions for common operations

def add_decision(
    timestamp: str,
    symbol: str,
    signal_source: str,
    signal_strength: int,
    direction: str,
    entry_price: float,
    position_size: int,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    confidence: int = 0,
    reasoning: str = "",
    rule_version: int = 1,
    status: str = "OPEN",
    db_path: Optional[Path] = None
) -> int:
    """Insert a new decision. Returns the decision ID."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO decisions
               (timestamp, symbol, signal_source, signal_strength, direction,
                entry_price, position_size, stop_loss, take_profit, confidence,
                reasoning, rule_version, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, symbol, signal_source, signal_strength, direction,
             entry_price, position_size, stop_loss, take_profit, confidence,
             reasoning, rule_version, status)
        )
        return cursor.lastrowid


def get_decision(decision_id: int, db_path: Optional[Path] = None) -> Optional[sqlite3.Row]:
    """Get a decision by ID."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()


def get_open_decisions(db_path: Optional[Path] = None) -> list:
    """Get all open decisions."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM decisions WHERE status = 'OPEN' ORDER BY timestamp DESC"
        ).fetchall()


def update_decision_status(
    decision_id: int,
    status: str,
    db_path: Optional[Path] = None
) -> bool:
    """Update a decision's status."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE decisions SET status = ? WHERE id = ?",
            (status, decision_id)
        )
        return cursor.rowcount > 0


def add_outcome(
    decision_id: int,
    exit_timestamp: str,
    exit_price: float,
    pnl_absolute: float,
    pnl_pct: float,
    hold_duration_minutes: int,
    exit_reason: str,
    market_outcome: str,
    db_path: Optional[Path] = None
) -> int:
    """Insert an outcome for a closed decision. Returns the outcome ID."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO outcomes
               (decision_id, exit_timestamp, exit_price, pnl_absolute, pnl_pct,
                hold_duration_minutes, exit_reason, market_outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (decision_id, exit_timestamp, exit_price, pnl_absolute, pnl_pct,
             hold_duration_minutes, exit_reason, market_outcome)
        )
        # Update decision status to CLOSED
        conn.execute(
            "UPDATE decisions SET status = 'CLOSED' WHERE id = ?",
            (decision_id,)
        )
        return cursor.lastrowid


def get_outcome(decision_id: int, db_path: Optional[Path] = None) -> Optional[sqlite3.Row]:
    """Get outcome for a decision."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM outcomes WHERE decision_id = ?", (decision_id,)
        ).fetchone()


def add_rule_version(
    description: str,
    params_json: str,
    parent_version: Optional[int] = None,
    db_path: Optional[Path] = None
) -> int:
    """Add a new rule version. Returns the version number."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO rule_versions (description, params_json, parent_version)
               VALUES (?, ?, ?)""",
            (description, params_json, parent_version)
        )
        return cursor.lastrowid


def get_rule_version(version: int, db_path: Optional[Path] = None) -> Optional[sqlite3.Row]:
    """Get a rule version by number."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM rule_versions WHERE version = ?", (version,)
        ).fetchone()


def get_latest_rule_version(db_path: Optional[Path] = None) -> Optional[sqlite3.Row]:
    """Get the most recent rule version."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM rule_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()


if __name__ == "__main__":
    # Allow running directly to initialize
    init_db()