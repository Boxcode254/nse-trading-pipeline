"""Decision Journal Database - SQLite operations for paper trading decisions, outcomes, and rule evolution."""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from contextlib import contextmanager


DECISIONS_DB_PATH = Path.home() / ".trading" / "learning" / "decisions.db"
SCHEMA_PATH = Path.home() / ".trading" / "learning" / "schema.sql"


@dataclass
class Decision:
    """A paper trade decision logged in the journal."""
    id: Optional[int] = None
    timestamp: str = ""  # ISO8601
    symbol: str = ""     # NSE symbol, e.g. RELIANCE, TCS
    signal_source: str = ""  # strategy name: 'momentum', 'mean_reversion', 'breakout', etc.
    signal_strength: int = 0  # 0-100
    direction: str = ""  # LONG/SHORT
    entry_price: float = 0.0
    position_size: int = 0  # shares
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    confidence: int = 0  # 0-100
    reasoning: str = ""  # why this trade
    rule_version: int = 1
    status: str = "OPEN"  # OPEN/CLOSED/EXPIRED
    created_at: Optional[str] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class Outcome:
    """Market outcome for a paper trade decision."""
    id: Optional[int] = None
    decision_id: int = 0
    exit_timestamp: str = ""
    exit_price: float = 0.0
    pnl_absolute: float = 0.0
    pnl_pct: float = 0.0
    hold_duration_minutes: int = 0
    exit_reason: str = ""  # STOP_LOSS, TAKE_PROFIT, TIME_EXPIRY, MANUAL
    market_outcome: str = ""  # UP, DOWN, SIDEWAYS
    created_at: Optional[str] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class RuleVersion:
    """A version of the trading rules."""
    version: Optional[int] = None
    created_at: Optional[str] = None
    description: str = ""
    params_json: str = ""  # JSON blob of all filter weights/thresholds
    parent_version: Optional[int] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.params_json:
            self.params_json = "{}"


class DecisionJournalDB:
    """SQLite database for the decision journal system."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DECISIONS_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database with schema if needed."""
        with self._conn() as conn:
            with open(SCHEMA_PATH) as f:
                conn.executescript(f.read())

    @contextmanager
    def _conn(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ========== Decision Operations ==========

    def add_decision(self, decision: Decision) -> int:
        """Insert a new decision. Returns the new ID."""
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO decisions
                   (timestamp, symbol, signal_source, signal_strength, direction,
                    entry_price, position_size, stop_loss, take_profit,
                    confidence, reasoning, rule_version, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (decision.timestamp, decision.symbol, decision.signal_source,
                 decision.signal_strength, decision.direction, decision.entry_price,
                 decision.position_size, decision.stop_loss, decision.take_profit,
                 decision.confidence, decision.reasoning, decision.rule_version,
                 decision.status, decision.created_at)
            )
            decision.id = cursor.lastrowid
            return decision.id

    def get_decision(self, decision_id: int) -> Optional[Decision]:
        """Get a decision by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            return self._row_to_decision(row) if row else None

    def get_open_decisions(self, symbol: Optional[str] = None) -> List[Decision]:
        """Get all open decisions, optionally filtered by symbol."""
        with self._conn() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM decisions WHERE status = 'OPEN' AND symbol = ? ORDER BY timestamp DESC",
                    (symbol,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM decisions WHERE status = 'OPEN' ORDER BY timestamp DESC"
                ).fetchall()
            return [self._row_to_decision(row) for row in rows]

    def get_decisions(
        self,
        symbol: Optional[str] = None,
        signal_source: Optional[str] = None,
        rule_version: Optional[int] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100
    ) -> List[Decision]:
        """Query decisions with optional filters."""
        query = "SELECT * FROM decisions WHERE 1=1"
        params = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if signal_source:
            query += " AND signal_source = ?"
            params.append(signal_source)
        if rule_version:
            query += " AND rule_version = ?"
            params.append(rule_version)
        if status:
            query += " AND status = ?"
            params.append(status)
        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_decision(row) for row in rows]

    def update_decision_status(self, decision_id: int, status: str) -> bool:
        """Update a decision's status (OPEN/CLOSED/EXPIRED)."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE decisions SET status = ? WHERE id = ?",
                (status, decision_id)
            )
            return cursor.rowcount > 0

    # ========== Outcome Operations ==========

    def add_outcome(self, outcome: Outcome) -> int:
        """Insert a new outcome. Returns the new ID."""
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO outcomes
                   (decision_id, exit_timestamp, exit_price, pnl_absolute, pnl_pct,
                    hold_duration_minutes, exit_reason, market_outcome, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (outcome.decision_id, outcome.exit_timestamp, outcome.exit_price,
                 outcome.pnl_absolute, outcome.pnl_pct, outcome.hold_duration_minutes,
                 outcome.exit_reason, outcome.market_outcome, outcome.created_at)
            )
            outcome.id = cursor.lastrowid
            return outcome.id

    def get_outcome(self, decision_id: int) -> Optional[Outcome]:
        """Get outcome for a decision."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM outcomes WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            return self._row_to_outcome(row) if row else None

    def get_outcomes(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100
    ) -> List[Outcome]:
        """Query outcomes with optional filters."""
        query = """SELECT o.* FROM outcomes o
                   JOIN decisions d ON o.decision_id = d.id
                   WHERE 1=1"""
        params = []

        if symbol:
            query += " AND d.symbol = ?"
            params.append(symbol)
        if start_date:
            query += " AND o.exit_timestamp >= ?"
            params.append(start_date)
        if end_date:
            query += " AND o.exit_timestamp <= ?"
            params.append(end_date)

        query += " ORDER BY o.exit_timestamp DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_outcome(row) for row in rows]

    # ========== Rule Version Operations ==========

    def add_rule_version(self, rule_version: RuleVersion) -> int:
        """Insert a new rule version. Returns the version number."""
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO rule_versions
                   (created_at, description, params_json, parent_version)
                   VALUES (?, ?, ?, ?)""",
                (rule_version.created_at, rule_version.description,
                 rule_version.params_json, rule_version.parent_version)
            )
            rule_version.version = cursor.lastrowid
            return rule_version.version

    def get_latest_rule_version(self) -> Optional[RuleVersion]:
        """Get the most recent rule version."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM rule_versions ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return self._row_to_rule_version(row) if row else None

    def get_rule_version(self, version: int) -> Optional[RuleVersion]:
        """Get a specific rule version."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM rule_versions WHERE version = ?", (version,)
            ).fetchone()
            return self._row_to_rule_version(row) if row else None

    # ========== Utility Methods ==========

    def _row_to_decision(self, row: sqlite3.Row) -> Decision:
        return Decision(
            id=row["id"],
            timestamp=row["timestamp"],
            symbol=row["symbol"],
            signal_source=row["signal_source"],
            signal_strength=row["signal_strength"],
            direction=row["direction"],
            entry_price=row["entry_price"],
            position_size=row["position_size"],
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            rule_version=row["rule_version"],
            status=row["status"],
            created_at=row["created_at"]
        )

    def _row_to_outcome(self, row: sqlite3.Row) -> Outcome:
        return Outcome(
            id=row["id"],
            decision_id=row["decision_id"],
            exit_timestamp=row["exit_timestamp"],
            exit_price=row["exit_price"],
            pnl_absolute=row["pnl_absolute"],
            pnl_pct=row["pnl_pct"],
            hold_duration_minutes=row["hold_duration_minutes"],
            exit_reason=row["exit_reason"],
            market_outcome=row["market_outcome"],
            created_at=row["created_at"]
        )

    def _row_to_rule_version(self, row: sqlite3.Row) -> RuleVersion:
        return RuleVersion(
            version=row["version"],
            created_at=row["created_at"],
            description=row["description"],
            params_json=row["params_json"],
            parent_version=row["parent_version"]
        )

    def close(self):
        """No-op for compatibility."""
        pass


# ============================================================
# Recommendation & Outcome Dataclasses (learning.db schema)
# ============================================================


@dataclass
class Recommendation:
    """A recommendation logged in the learning database."""
    symbol: str = ""
    date: str = ""  # YYYY-MM-DD
    confidence: float = 0.5
    recommendation: str = "HOLD"  # BUY, SELL, HOLD
    score: float = 0.0
    factors: Dict[str, Any] = None
    factors_hash: str = ""
    timestamp: str = ""  # ISO8601
    created_at: str = ""
    id: Optional[int] = None

    def __post_init__(self):
        if self.factors is None:
            self.factors = {}
        if not self.factors_hash and self.factors:
            import hashlib
            raw = json.dumps(self.factors, sort_keys=True)
            self.factors_hash = hashlib.sha256(raw.encode()).hexdigest()[:12]
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class Outcome:
    """An outcome record in the learning database."""
    symbol: str = ""
    date: str = ""  # YYYY-MM-DD (recommendation date)
    market_outcome: str = ""  # UP, DOWN, FLAT
    expected_return: Optional[float] = None
    actual_return: Optional[float] = None
    time_to_target: Optional[int] = None
    success: bool = False
    evaluated_at: str = ""
    created_at: str = ""
    id: Optional[int] = None

    def __post_init__(self):
        if not self.evaluated_at:
            self.evaluated_at = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


# ============================================================
# LearningDB — connects to learning.db (recommendations, outcomes, decisions)
# ============================================================


LEARNING_DB_PATH = Path.home() / ".trading" / "learning" / "learning.db"


class LearningDB:
    """Database layer for the learning system (learning.db)."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or LEARNING_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure the learning schema exists (learning.db specific tables)."""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    recommendation TEXT NOT NULL,
                    score REAL NOT NULL,
                    factors_hash TEXT NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_rec_symbol_date
                    ON recommendations(symbol, date);
                CREATE INDEX IF NOT EXISTS idx_rec_factors_hash
                    ON recommendations(factors_hash);
                CREATE INDEX IF NOT EXISTS idx_rec_timestamp
                    ON recommendations(timestamp);
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    market_outcome TEXT NOT NULL,
                    expected_return REAL,
                    actual_return REAL,
                    time_to_target INTEGER,
                    success BOOLEAN NOT NULL,
                    evaluated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_outc_symbol_date
                    ON outcomes(symbol, date);
                CREATE INDEX IF NOT EXISTS idx_outc_evaluated
                    ON outcomes(evaluated_at);
                CREATE INDEX IF NOT EXISTS idx_outc_success
                    ON outcomes(success);
                CREATE VIEW IF NOT EXISTS monthly_stats AS
                SELECT
                    strftime('%%Y-%%m', r.date) AS month,
                    COUNT(DISTINCT r.symbol) AS unique_symbols,
                    COUNT(r.id) AS total_recommendations,
                    SUM(CASE WHEN r.recommendation = 'BUY' THEN 1 ELSE 0 END) AS buy_count,
                    SUM(CASE WHEN r.recommendation = 'SELL' THEN 1 ELSE 0 END) AS sell_count,
                    SUM(CASE WHEN r.recommendation = 'HOLD' THEN 1 ELSE 0 END) AS hold_count,
                    AVG(r.confidence) AS avg_confidence,
                    AVG(r.score) AS avg_score,
                    COUNT(o.id) AS evaluated_outcomes,
                    SUM(CASE WHEN o.success = 1 THEN 1 ELSE 0 END) AS successful_outcomes,
                    ROUND(100.0 * SUM(CASE WHEN o.success = 1 THEN 1 ELSE 0 END)
                        / NULLIF(COUNT(o.id), 0), 2) AS success_rate_pct,
                    AVG(o.actual_return) AS avg_actual_return,
                    AVG(o.expected_return) AS avg_expected_return,
                    AVG(o.time_to_target) AS avg_time_to_target_days
                FROM recommendations r
                LEFT JOIN outcomes o ON r.symbol = o.symbol AND r.date = o.date
                GROUP BY strftime('%%Y-%%m', r.date);
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal_source TEXT NOT NULL,
                    signal_strength INTEGER NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    position_size INTEGER NOT NULL,
                    stop_loss REAL,
                    take_profit REAL,
                    confidence INTEGER NOT NULL,
                    reasoning TEXT,
                    rule_version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Recommendations ──────────────────────────────────────

    def add_recommendation(self, rec: Recommendation) -> int:
        """Insert a new recommendation. Returns its ID."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO recommendations
                   (symbol, date, confidence, recommendation, score, factors_hash, timestamp, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rec.symbol, rec.date, rec.confidence, rec.recommendation,
                 rec.score, rec.factors_hash, rec.timestamp, rec.created_at)
            )
            rec.id = cur.lastrowid
            return rec.id

    def upsert_recommendation(self, rec: Recommendation) -> int:
        """Insert a recommendation, deduplicating by factors_hash."""
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM recommendations WHERE symbol = ? AND date = ? AND factors_hash = ?",
                (rec.symbol, rec.date, rec.factors_hash)
            ).fetchone()
            if existing:
                rec.id = existing["id"]
                conn.execute(
                    """UPDATE recommendations SET confidence=?, recommendation=?, score=?, timestamp=?
                       WHERE id=?""",
                    (rec.confidence, rec.recommendation, rec.score, rec.timestamp, rec.id)
                )
                return rec.id
            return self.add_recommendation(rec)

    def get_recommendation(self, symbol: str, date: str) -> Optional[Recommendation]:
        """Get a recommendation by symbol+date (most recent)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM recommendations WHERE symbol = ? AND date = ? ORDER BY id DESC LIMIT 1",
                (symbol.upper(), date)
            ).fetchone()
            return self._row_to_rec(row) if row else None

    def get_recommendations(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        recommendation: Optional[str] = None,
        limit: int = 100,
    ) -> List[Recommendation]:
        """Query recommendations with optional filters."""
        query = "SELECT * FROM recommendations WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        if recommendation:
            query += " AND recommendation = ?"
            params.append(recommendation.upper())
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_rec(r) for r in rows]

    # ── Outcomes ─────────────────────────────────────────────

    def add_outcome(self, outcome: Outcome) -> int:
        """Insert an outcome. Returns its ID."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO outcomes
                   (symbol, date, market_outcome, expected_return, actual_return,
                    time_to_target, success, evaluated_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (outcome.symbol, outcome.date, outcome.market_outcome,
                 outcome.expected_return, outcome.actual_return,
                 outcome.time_to_target, int(outcome.success),
                 outcome.evaluated_at, outcome.created_at)
            )
            outcome.id = cur.lastrowid
            return outcome.id

    def get_outcomes(self, symbol: Optional[str] = None, date: Optional[str] = None) -> List[Outcome]:
        """Get outcomes, optionally filtered by symbol and/or date."""
        query = "SELECT * FROM outcomes WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if date:
            query += " AND date = ?"
            params.append(date)
        query += " ORDER BY evaluated_at DESC"

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_outcome(r) for r in rows]

    # ── Stats ────────────────────────────────────────────────

    def get_overall_stats(self) -> Dict[str, Any]:
        """Get overall performance stats from the monthly_stats view."""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COALESCE(SUM(total_recommendations), 0) as total_recommendations,
                    COALESCE(SUM(buy_count), 0) as buy_count,
                    COALESCE(SUM(sell_count), 0) as sell_count,
                    COALESCE(SUM(hold_count), 0) as hold_count,
                    COALESCE(AVG(avg_confidence), 0) as avg_confidence,
                    COALESCE(AVG(avg_score), 0) as avg_score,
                    COALESCE(SUM(evaluated_outcomes), 0) as evaluated_outcomes,
                    COALESCE(SUM(successful_outcomes), 0) as successful_outcomes,
                    CASE WHEN SUM(evaluated_outcomes) > 0
                         THEN ROUND(100.0 * SUM(successful_outcomes) / SUM(evaluated_outcomes), 2)
                         ELSE 0 END as success_rate_pct,
                    COALESCE(AVG(avg_actual_return), 0) as avg_actual_return,
                    COALESCE(AVG(avg_expected_return), 0) as avg_expected_return,
                    COALESCE(AVG(avg_time_to_target_days), 0) as avg_time_to_target_days
                FROM monthly_stats
            """).fetchone()
            return dict(row) if row else {}

    def get_monthly_stats(self, months: int = 12) -> List[Dict[str, Any]]:
        """Get monthly stats from the monthly_stats view."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM monthly_stats LIMIT ?", (months,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_symbol_performance(self, symbol: str) -> Dict[str, Any]:
        """Get performance stats for a single symbol."""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as recommendation_count,
                    AVG(confidence) as avg_confidence,
                    AVG(score) as avg_score,
                    SUM(CASE WHEN o.id IS NOT NULL THEN 1 ELSE 0 END) as evaluated,
                    SUM(CASE WHEN o.success = 1 THEN 1 ELSE 0 END) as successful,
                    CASE WHEN SUM(CASE WHEN o.id IS NOT NULL THEN 1 ELSE 0 END) > 0
                         THEN ROUND(100.0 * SUM(CASE WHEN o.success = 1 THEN 1 ELSE 0 END)
                              / NULLIF(SUM(CASE WHEN o.id IS NOT NULL THEN 1 ELSE 0 END), 0), 2)
                         ELSE 0 END as success_rate_pct,
                    AVG(o.actual_return) as avg_return,
                    AVG(o.time_to_target) as avg_time_to_target
                FROM recommendations r
                LEFT JOIN outcomes o ON r.symbol = o.symbol AND r.date = o.date
                WHERE r.symbol = ?
            """, (symbol.upper(),)).fetchone()
            return dict(row) if row else {}

    # ── Row mappers ──────────────────────────────────────────

    @staticmethod
    def _row_to_rec(row: sqlite3.Row) -> Recommendation:
        return Recommendation(
            id=row["id"],
            symbol=row["symbol"],
            date=row["date"],
            confidence=row["confidence"],
            recommendation=row["recommendation"],
            score=row["score"],
            factors_hash=row["factors_hash"],
            timestamp=row["timestamp"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_outcome(row: sqlite3.Row) -> Outcome:
        return Outcome(
            id=row["id"],
            symbol=row["symbol"],
            date=row["date"],
            market_outcome=row["market_outcome"],
            expected_return=row["expected_return"],
            actual_return=row["actual_return"],
            time_to_target=row["time_to_target"],
            success=bool(row["success"]),
            evaluated_at=row["evaluated_at"],
            created_at=row["created_at"],
        )

    def close(self):
        pass


# Backward-compatible aliases
# (LearningDB class is defined above — no alias needed)
MonthlyStats = object  # placeholder for legacy import


# ── Singleton access (returns LearningDB, the learning.db wrapper) ──
#
# get_db() returns a LearningDB instance connected to learning.db.
# DecisionJournalDB is still available as its own class for paper trading
# operations on decisions.db.


_default_learning_db_instance: Optional[LearningDB] = None


def get_db() -> LearningDB:
    """Get the default LearningDB instance (learning.db)."""
    global _default_learning_db_instance
    if _default_learning_db_instance is None:
        _default_learning_db_instance = LearningDB()
    return _default_learning_db_instance


# ========== DecisionJournalDB convenience functions ==========

_default_decision_db: Optional[DecisionJournalDB] = None


def get_decision_db() -> DecisionJournalDB:
    """Get the default DecisionJournalDB instance (decisions.db)."""
    global _default_decision_db
    if _default_decision_db is None:
        _default_decision_db = DecisionJournalDB()
    return _default_decision_db


def get_connection() -> sqlite3.Connection:
    """Get a raw connection to the decision database (for compatibility)."""
    db = get_decision_db()
    return sqlite3.connect(db.db_path)


def get_conn() -> sqlite3.Connection:
    """Alias for get_connection."""
    return get_connection()


def init_db() -> DecisionJournalDB:
    """Initialize and return the decision journal database."""
    return DecisionJournalDB()


# Convenience wrapper functions
def add_decision(decision: Decision) -> int:
    return get_decision_db().add_decision(decision)


def get_decision(decision_id: int) -> Optional[Decision]:
    return get_decision_db().get_decision(decision_id)


def get_open_decisions(symbol: Optional[str] = None) -> List[Decision]:
    return get_decision_db().get_open_decisions(symbol)


def update_decision_status(decision_id: int, status: str) -> bool:
    return get_decision_db().update_decision_status(decision_id, status)


def add_outcome(outcome: object) -> int:
    return get_decision_db().add_outcome(outcome)


def get_outcome(decision_id: int) -> object:
    return get_decision_db().get_outcome(decision_id)


def add_rule_version(rule_version: object) -> int:
    return get_decision_db().add_rule_version(rule_version)


def get_rule_version(version: int) -> object:
    return get_decision_db().get_rule_version(version)


def get_latest_rule_version() -> object:
    return get_decision_db().get_latest_rule_version()