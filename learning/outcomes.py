"""Outcome Recording System for Trading Recommendations.

Tracks daily price closes for recommended assets, calculates actual returns vs expected,
time to target, success boolean. Integrates with portfolio snapshots.
Records market outcome tags: UP/DOWN/SIDEWAYS.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal
from contextlib import contextmanager

# Market outcome tags
MarketOutcome = Literal["UP", "DOWN", "SIDEWAYS"]
RecommendationType = Literal["BUY", "SELL", "HOLD"]


@dataclass
class DailyPriceClose:
    """Daily closing price record for a recommended asset."""
    symbol: str
    date: str  # YYYY-MM-DD
    close_price: float
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    volume: Optional[float] = None
    source: str = "yfinance"
    recorded_at: str = field(default_factory=lambda: datetime.now().isoformat())
    id: Optional[int] = None


@dataclass
class RecommendationOutcome:
    """Complete outcome record for a recommendation."""
    recommendation_id: int
    symbol: str
    recommendation_date: str  # YYYY-MM-DD
    recommendation_type: RecommendationType
    confidence: float
    expected_return_pct: Optional[float] = None
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    holding_period_days: int = 30  # default expected holding period
    
    # Outcome fields (populated when evaluated)
    market_outcome: Optional[MarketOutcome] = None
    actual_return_pct: Optional[float] = None
    actual_return_abs: Optional[float] = None
    time_to_target_days: Optional[int] = None
    time_to_stop_loss_days: Optional[int] = None
    success: Optional[bool] = None
    max_favorable_excursion_pct: Optional[float] = None  # max profit during hold
    max_adverse_excursion_pct: Optional[float] = None   # max loss during hold
    evaluated_at: Optional[str] = None
    evaluation_date: Optional[str] = None  # YYYY-MM-DD when evaluated
    
    # Portfolio integration
    portfolio_snapshot_id: Optional[int] = None
    portfolio_value_at_rec: Optional[float] = None
    portfolio_value_at_eval: Optional[float] = None
    
    # Database fields
    id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class PortfolioSnapshotRef:
    """Reference to a portfolio snapshot for outcome correlation."""
    snapshot_id: int
    timestamp: str
    total_value: float
    cash: float
    holdings_value: float
    symbols: List[str]  # symbols held at snapshot time


class OutcomeRecorder:
    """Records and evaluates trading outcomes against recommendations."""
    
    def __init__(
        self,
        learning_db_path: Optional[Path] = None,
        portfolio_dir: Optional[Path] = None,
        price_db_path: Optional[Path] = None,
    ):
        self.learning_db = learning_db_path or Path.home() / ".trading" / "learning" / "learning.db"
        self.portfolio_dir = portfolio_dir or Path.home() / ".trading" / "portfolio"
        self.price_db = price_db_path or Path.home() / ".trading" / "learning" / "prices.db"
        
        self._init_price_db()
    
    def _init_price_db(self):
        """Initialize the daily price closes database."""
        self.price_db.parent.mkdir(parents=True, exist_ok=True)
        with self._price_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_closes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,  -- YYYY-MM-DD
                    close_price REAL NOT NULL,
                    high_price REAL,
                    low_price REAL,
                    volume REAL,
                    source TEXT DEFAULT 'yfinance',
                    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(symbol, date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_closes_symbol_date 
                ON daily_closes(symbol, date)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recommendation_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recommendation_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    recommendation_date TEXT NOT NULL,
                    recommendation_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    expected_return_pct REAL,
                    target_price REAL,
                    stop_loss REAL,
                    holding_period_days INTEGER DEFAULT 30,
                    
                    market_outcome TEXT,
                    actual_return_pct REAL,
                    actual_return_abs REAL,
                    time_to_target_days INTEGER,
                    time_to_stop_loss_days INTEGER,
                    success INTEGER,  -- 0 or 1
                    max_favorable_excursion_pct REAL,
                    max_adverse_excursion_pct REAL,
                    evaluated_at TEXT,
                    evaluation_date TEXT,
                    
                    portfolio_snapshot_id INTEGER,
                    portfolio_value_at_rec REAL,
                    portfolio_value_at_eval REAL,
                    
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(recommendation_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_outcomes_symbol_date 
                ON recommendation_outcomes(symbol, recommendation_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_outcomes_evaluated 
                ON recommendation_outcomes(evaluated_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_outcomes_success 
                ON recommendation_outcomes(success)
            """)
    
    @contextmanager
    def _price_conn(self):
        """Context manager for price database connections."""
        conn = sqlite3.connect(self.price_db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    @contextmanager
    def _learning_conn(self):
        """Context manager for learning database connections."""
        conn = sqlite3.connect(self.learning_db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    # ============================================================
    # Daily Price Recording
    # ============================================================
    
    def record_daily_close(
        self,
        symbol: str,
        date: str,
        close_price: float,
        high_price: Optional[float] = None,
        low_price: Optional[float] = None,
        volume: Optional[float] = None,
        source: str = "yfinance",
    ) -> DailyPriceClose:
        """Record a daily closing price for a symbol."""
        close = DailyPriceClose(
            symbol=symbol.upper(),
            date=date,
            close_price=close_price,
            high_price=high_price,
            low_price=low_price,
            volume=volume,
            source=source,
        )
        
        with self._price_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_closes
                (symbol, date, close_price, high_price, low_price, volume, source, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                close.symbol, close.date, close.close_price,
                close.high_price, close.low_price, close.volume,
                close.source, close.recorded_at
            ))
        return close
    
    def record_daily_closes_batch(
        self,
        closes: List[DailyPriceClose],
    ) -> List[DailyPriceClose]:
        """Record multiple daily closes in a single transaction."""
        with self._price_conn() as conn:
            for close in closes:
                conn.execute("""
                    INSERT OR REPLACE INTO daily_closes
                    (symbol, date, close_price, high_price, low_price, volume, source, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    close.symbol, close.date, close.close_price,
                    close.high_price, close.low_price, close.volume,
                    close.source, close.recorded_at
                ))
        return closes
    
    def get_daily_close(self, symbol: str, date: str) -> Optional[DailyPriceClose]:
        """Get a single daily close record."""
        with self._price_conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_closes WHERE symbol = ? AND date = ?",
                (symbol.upper(), date)
            ).fetchone()
            if row:
                return DailyPriceClose(**dict(row))
        return None
    
    def get_price_series(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> List[DailyPriceClose]:
        """Get price series for a symbol over a date range."""
        end = end_date or date.today().isoformat()
        with self._price_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM daily_closes 
                   WHERE symbol = ? AND date >= ? AND date <= ?
                   ORDER BY date""",
                (symbol.upper(), start_date, end)
            ).fetchall()
            return [DailyPriceClose(**dict(r)) for r in rows]
    
    def get_latest_price(self, symbol: str) -> Optional[DailyPriceClose]:
        """Get the most recent price for a symbol."""
        with self._price_conn() as conn:
            row = conn.execute(
                """SELECT * FROM daily_closes 
                   WHERE symbol = ? 
                   ORDER BY date DESC LIMIT 1""",
                (symbol.upper(),)
            ).fetchone()
            if row:
                return DailyPriceClose(**dict(row))
        return None
    
    # ============================================================
    # Portfolio Snapshot Integration
    # ============================================================
    
    def get_latest_portfolio_snapshot(self) -> Optional[PortfolioSnapshotRef]:
        """Get the most recent portfolio snapshot."""
        snapshots_path = self.portfolio_dir / "snapshots.json"
        if not snapshots_path.exists():
            return None
        
        with open(snapshots_path) as f:
            snapshots = json.load(f)
        
        if not snapshots:
            return None
        
        latest = snapshots[-1]
        symbols = list(latest.get("prices", {}).keys())
        
        return PortfolioSnapshotRef(
            snapshot_id=len(snapshots) - 1,
            timestamp=latest["timestamp"],
            total_value=latest["total_value"],
            cash=latest["cash"],
            holdings_value=latest["holdings_value"],
            symbols=symbols,
        )
    
    def get_portfolio_snapshot_at(self, timestamp: str) -> Optional[PortfolioSnapshotRef]:
        """Get portfolio snapshot closest to a given timestamp."""
        snapshots_path = self.portfolio_dir / "snapshots.json"
        if not snapshots_path.exists():
            return None
        
        with open(snapshots_path) as f:
            snapshots = json.load(f)
        
        if not snapshots:
            return None
        
        # Find closest snapshot before or at timestamp
        target = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        closest = None
        min_diff = float('inf')
        
        for i, snap in enumerate(snapshots):
            snap_time = datetime.fromisoformat(snap["timestamp"].replace('Z', '+00:00'))
            if snap_time.tzinfo is None:
                snap_time = snap_time.replace(tzinfo=timezone.utc)
            diff = (target - snap_time).total_seconds()
            if diff >= 0 and diff < min_diff:
                min_diff = diff
                closest = (i, snap)
        
        if closest:
            i, snap = closest
            symbols = list(snap.get("prices", {}).keys())
            return PortfolioSnapshotRef(
                snapshot_id=i,
                timestamp=snap["timestamp"],
                total_value=snap["total_value"],
                cash=snap["cash"],
                holdings_value=snap["holdings_value"],
                symbols=symbols,
            )
        return None
    
    # ============================================================
    # Recommendation Outcome Management
    # ============================================================
    
    def create_outcome_from_recommendation(
        self,
        recommendation_id: int,
        expected_return_pct: Optional[float] = None,
        target_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        holding_period_days: int = 30,
    ) -> RecommendationOutcome:
        """Create an outcome tracking record from a recommendation."""
        with self._learning_conn() as conn:
            row = conn.execute(
                "SELECT * FROM recommendations WHERE id = ?",
                (recommendation_id,)
            ).fetchone()
            
            if not row:
                raise ValueError(f"Recommendation {recommendation_id} not found")
            
            rec = dict(row)
        
        # Get portfolio snapshot at recommendation time
        portfolio_snap = self.get_portfolio_snapshot_at(rec["timestamp"])
        
        outcome = RecommendationOutcome(
            recommendation_id=recommendation_id,
            symbol=rec["symbol"],
            recommendation_date=rec["date"],
            recommendation_type=rec["recommendation"],
            confidence=rec["confidence"],
            expected_return_pct=expected_return_pct,
            target_price=target_price,
            stop_loss=stop_loss,
            holding_period_days=holding_period_days,
            portfolio_snapshot_id=portfolio_snap.snapshot_id if portfolio_snap else None,
            portfolio_value_at_rec=portfolio_snap.total_value if portfolio_snap else None,
        )
        
        with self._price_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO recommendation_outcomes
                (recommendation_id, symbol, recommendation_date, recommendation_type,
                 confidence, expected_return_pct, target_price, stop_loss,
                 holding_period_days, portfolio_snapshot_id, portfolio_value_at_rec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                outcome.recommendation_id, outcome.symbol, outcome.recommendation_date,
                outcome.recommendation_type, outcome.confidence, outcome.expected_return_pct,
                outcome.target_price, outcome.stop_loss, outcome.holding_period_days,
                outcome.portfolio_snapshot_id, outcome.portfolio_value_at_rec,
            ))
        
        return outcome
    
    def evaluate_outcome(
        self,
        recommendation_id: int,
        evaluation_date: Optional[str] = None,
        force: bool = False,
    ) -> RecommendationOutcome:
        """Evaluate a recommendation's outcome using recorded price data."""
        eval_date = evaluation_date or date.today().isoformat()
        
        with self._price_conn() as conn:
            # Get the outcome record
            row = conn.execute(
                "SELECT * FROM recommendation_outcomes WHERE recommendation_id = ?",
                (recommendation_id,)
            ).fetchone()
            
            if not row:
                raise ValueError(f"No outcome record for recommendation {recommendation_id}")
            
            outcome = RecommendationOutcome(**dict(row))
            
            # Check if already evaluated
            if outcome.evaluated_at and not force:
                return outcome
            
            # Get price series from recommendation date to evaluation date
            prices = self.get_price_series(
                outcome.symbol,
                outcome.recommendation_date,
                eval_date,
            )
            
            if len(prices) < 2:
                # Not enough data to evaluate
                return outcome
            
            entry_price = prices[0].close_price
            
            # Determine market outcome
            final_price = prices[-1].close_price
            price_change_pct = ((final_price - entry_price) / entry_price) * 100
            
            # Classify market outcome
            if price_change_pct > 1.0:
                market_outcome: MarketOutcome = "UP"
            elif price_change_pct < -1.0:
                market_outcome = "DOWN"
            else:
                market_outcome = "SIDEWAYS"
            
            # Calculate success based on recommendation type
            if outcome.recommendation_type == "BUY":
                success = market_outcome == "UP"
            elif outcome.recommendation_type == "SELL":
                success = market_outcome == "DOWN"
            else:  # HOLD
                success = market_outcome == "SIDEWAYS"
            
            # Calculate actual return
            actual_return_pct = price_change_pct if outcome.recommendation_type == "BUY" else -price_change_pct
            actual_return_abs = (final_price - entry_price) / entry_price * 100
            
            # Time to target / stop loss
            time_to_target = None
            time_to_stop = None
            max_favorable = 0.0
            max_adverse = 0.0
            
            if outcome.target_price or outcome.stop_loss:
                for i, p in enumerate(prices):
                    pct_from_entry = ((p.close_price - entry_price) / entry_price) * 100
                    
                    # Track max favorable/adverse excursion
                    if outcome.recommendation_type == "BUY":
                        max_favorable = max(max_favorable, pct_from_entry)
                        max_adverse = min(max_adverse, pct_from_entry)
                        
                        if outcome.target_price and p.close_price >= outcome.target_price and time_to_target is None:
                            time_to_target = i
                        if outcome.stop_loss and p.close_price <= outcome.stop_loss and time_to_stop is None:
                            time_to_stop = i
                    else:  # SELL
                        max_favorable = max(max_favorable, -pct_from_entry)
                        max_adverse = min(max_adverse, -pct_from_entry)
                        
                        if outcome.target_price and p.close_price <= outcome.target_price and time_to_target is None:
                            time_to_target = i
                        if outcome.stop_loss and p.close_price >= outcome.stop_loss and time_to_stop is None:
                            time_to_stop = i
            
            # Get portfolio value at evaluation time
            portfolio_snap = self.get_portfolio_snapshot_at(eval_date)
            portfolio_value_at_eval = portfolio_snap.total_value if portfolio_snap else None
            
            # Update outcome
            outcome.market_outcome = market_outcome
            outcome.actual_return_pct = round(actual_return_pct, 2)
            outcome.actual_return_abs = round(actual_return_abs, 2)
            outcome.time_to_target_days = time_to_target
            outcome.time_to_stop_loss_days = time_to_stop
            outcome.success = success
            outcome.max_favorable_excursion_pct = round(max_favorable, 2) if max_favorable > 0 else None
            outcome.max_adverse_excursion_pct = round(max_adverse, 2) if max_adverse < 0 else None
            outcome.evaluated_at = datetime.now().isoformat()
            outcome.evaluation_date = eval_date
            outcome.portfolio_value_at_eval = portfolio_value_at_eval
            
            # Persist
            conn.execute("""
                UPDATE recommendation_outcomes SET
                    market_outcome = ?, actual_return_pct = ?, actual_return_abs = ?,
                    time_to_target_days = ?, time_to_stop_loss_days = ?, success = ?,
                    max_favorable_excursion_pct = ?, max_adverse_excursion_pct = ?,
                    evaluated_at = ?, evaluation_date = ?,
                    portfolio_value_at_eval = ?, updated_at = ?
                WHERE recommendation_id = ?
            """, (
                outcome.market_outcome, outcome.actual_return_pct, outcome.actual_return_abs,
                outcome.time_to_target_days, outcome.time_to_stop_loss_days,
                1 if outcome.success else 0,
                outcome.max_favorable_excursion_pct, outcome.max_adverse_excursion_pct,
                outcome.evaluated_at, outcome.evaluation_date,
                outcome.portfolio_value_at_eval, datetime.now().isoformat(),
                outcome.recommendation_id,
            ))
        
        return outcome
    
    def evaluate_all_pending(
        self,
        evaluation_date: Optional[str] = None,
        max_holding_days: Optional[int] = None,
    ) -> List[RecommendationOutcome]:
        """Evaluate all recommendations that haven't been evaluated yet."""
        eval_date = evaluation_date or date.today().isoformat()
        results = []
        
        with self._price_conn() as conn:
            query = "SELECT recommendation_id FROM recommendation_outcomes WHERE evaluated_at IS NULL"
            if max_holding_days:
                # Only evaluate if recommendation date is old enough
                cutoff = (date.fromisoformat(eval_date) - timedelta(days=max_holding_days)).isoformat()
                query += f" AND recommendation_date <= '{cutoff}'"
            
            rows = conn.execute(query).fetchall()
        
        for row in rows:
            try:
                outcome = self.evaluate_outcome(row["recommendation_id"], eval_date)
                if outcome.evaluated_at:
                    results.append(outcome)
            except Exception as e:
                # Log but continue
                print(f"Failed to evaluate outcome {row['recommendation_id']}: {e}")
        
        return results
    
    def get_outcome(self, recommendation_id: int) -> Optional[RecommendationOutcome]:
        """Get an outcome by recommendation ID."""
        with self._price_conn() as conn:
            row = conn.execute(
                "SELECT * FROM recommendation_outcomes WHERE recommendation_id = ?",
                (recommendation_id,)
            ).fetchone()
            if row:
                return RecommendationOutcome(**dict(row))
        return None
    
    def get_outcomes(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        evaluated_only: bool = True,
        limit: int = 100,
    ) -> List[RecommendationOutcome]:
        """Query outcomes with filters."""
        query = "SELECT * FROM recommendation_outcomes WHERE 1=1"
        params = []
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if start_date:
            query += " AND recommendation_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND recommendation_date <= ?"
            params.append(end_date)
        if evaluated_only:
            query += " AND evaluated_at IS NOT NULL"
        
        query += " ORDER BY recommendation_date DESC LIMIT ?"
        params.append(limit)
        
        with self._price_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [RecommendationOutcome(**dict(r)) for r in rows]
    
    # ============================================================
    # Analytics & Reporting
    # ============================================================
    
    def get_performance_summary(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get performance summary statistics."""
        outcomes = self.get_outcomes(symbol, start_date, end_date, evaluated_only=True, limit=10000)
        
        if not outcomes:
            return {
                "total_evaluated": 0,
                "message": "No evaluated outcomes found",
            }
        
        successful = [o for o in outcomes if o.success]
        failed = [o for o in outcomes if not o.success]
        
        returns = [o.actual_return_pct for o in outcomes if o.actual_return_pct is not None]
        times_to_target = [o.time_to_target_days for o in outcomes if o.time_to_target_days is not None]
        
        by_type: Dict[str, Dict] = {}
        for o in outcomes:
            if o.recommendation_type not in by_type:
                by_type[o.recommendation_type] = {"total": 0, "success": 0, "returns": []}
            by_type[o.recommendation_type]["total"] += 1
            if o.success:
                by_type[o.recommendation_type]["success"] += 1
            if o.actual_return_pct is not None:
                by_type[o.recommendation_type]["returns"].append(o.actual_return_pct)
        
        by_outcome: Dict[str, int] = {}
        for o in outcomes:
            if o.market_outcome:
                by_outcome[o.market_outcome] = by_outcome.get(o.market_outcome, 0) + 1
        
        return {
            "total_evaluated": len(outcomes),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate_pct": round(len(successful) / len(outcomes) * 100, 2),
            "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else 0,
            "median_return_pct": round(sorted(returns)[len(returns)//2], 2) if returns else 0,
            "avg_time_to_target_days": round(sum(times_to_target) / len(times_to_target), 1) if times_to_target else None,
            "by_recommendation_type": {
                k: {
                    "total": v["total"],
                    "success_rate_pct": round(v["success"] / v["total"] * 100, 2) if v["total"] else 0,
                    "avg_return_pct": round(sum(v["returns"]) / len(v["returns"]), 2) if v["returns"] else 0,
                }
                for k, v in by_type.items()
            },
            "by_market_outcome": by_outcome,
            "best_trade_pct": round(max(returns), 2) if returns else 0,
            "worst_trade_pct": round(min(returns), 2) if returns else 0,
        }
    
    def get_symbol_outcomes(
        self,
        symbol: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get detailed outcome records for a symbol."""
        outcomes = self.get_outcomes(symbol=symbol, limit=limit)
        return [
            {
                "recommendation_id": o.recommendation_id,
                "symbol": o.symbol,
                "date": o.recommendation_date,
                "type": o.recommendation_type,
                "confidence": o.confidence,
                "expected_return": o.expected_return_pct,
                "actual_return": o.actual_return_pct,
                "market_outcome": o.market_outcome,
                "success": o.success,
                "time_to_target": o.time_to_target_days,
                "max_favorable_pct": o.max_favorable_excursion_pct,
                "max_adverse_pct": o.max_adverse_excursion_pct,
                "evaluated_at": o.evaluated_at,
            }
            for o in outcomes
        ]


# Convenience functions for quick access
_default_recorder: Optional[OutcomeRecorder] = None


def get_recorder() -> OutcomeRecorder:
    """Get the default outcome recorder instance (singleton)."""
    global _default_recorder
    if _default_recorder is None:
        _default_recorder = OutcomeRecorder()
    return _default_recorder


def record_daily_closes_from_market_service(symbols: List[str]) -> Dict[str, DailyPriceClose]:
    """Fetch latest prices from market service and record as daily closes."""
    import sys
    sys.path.insert(0, str(Path.home() / ".trading"))
    from trading.services import market
    
    recorder = get_recorder()
    today = date.today().isoformat()
    results = {}
    
    for symbol in symbols:
        try:
            info = market.latest_price(symbol)
            price = info.get("price")
            if price is not None:
                close = recorder.record_daily_close(
                    symbol=symbol,
                    date=today,
                    close_price=float(price),
                    source=info.get("source", "yfinance"),
                )
                results[symbol] = close
        except Exception as e:
            print(f"Failed to record price for {symbol}: {e}")
    
    return results


if __name__ == "__main__":
    # Quick test
    recorder = OutcomeRecorder()
    print("OutcomeRecorder initialized")
    print(f"Price DB: {recorder.price_db}")
    print(f"Learning DB: {recorder.learning_db}")
    print(f"Portfolio dir: {recorder.portfolio_dir}")