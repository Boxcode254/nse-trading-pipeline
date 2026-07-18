"""Paper Trading Engine - shadows live signals and tracks virtual positions."""

from __future__ import annotations

import sqlite3
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional, Literal, Self
from pathlib import Path
from enum import Enum

from trading.learning.db import (
    get_connection,
    add_outcome,
    get_outcome,
    update_decision_status,
    get_open_decisions,
    get_decision,
)


class PositionEvent(str, Enum):
    """Position event types returned by update_positions()."""
    STOP_HIT = "STOP_HIT"
    TARGET_HIT = "TARGET_HIT"
    TIME_EXPIRED = "TIME_EXPIRED"
    NONE = "NONE"


class Direction(str, Enum):
    """Position direction."""
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    """Exit reason for outcome."""
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIME_EXPIRY = "TIME_EXPIRY"
    MANUAL = "MANUAL"


class MarketOutcome(str, Enum):
    """Market outcome relative to entry."""
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


@dataclass
class Decision:
    """Trading decision from the decision journal."""
    id: int
    timestamp: str
    symbol: str
    signal_source: str
    signal_strength: int
    direction: Direction
    entry_price: float
    position_size: int
    stop_loss: Optional[float]
    take_profit: Optional[float]
    confidence: int
    reasoning: str
    rule_version: int
    status: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        """Create Decision from sqlite3.Row."""
        return cls(
            id=row["id"],
            timestamp=row["timestamp"],
            symbol=row["symbol"],
            signal_source=row["signal_source"],
            signal_strength=row["signal_strength"],
            direction=Direction(row["direction"]),
            entry_price=row["entry_price"],
            position_size=row["position_size"],
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            rule_version=row["rule_version"],
            status=row["status"],
            created_at=row["created_at"],
        )


@dataclass
class Position:
    """Virtual position in the paper trading engine."""
    id: str  # UUID
    decision_id: int
    symbol: str
    direction: Direction
    entry_price: float
    position_size: int
    stop_loss: Optional[float]
    take_profit: Optional[float]
    entry_time: datetime
    expiry_hours: int = 24
    is_open: bool = True
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Check if position has expired (24h default)."""
        if now is None:
            now = datetime.utcnow()
        return now >= self.entry_time + timedelta(hours=self.expiry_hours)

    def check_stop_hit(self, current_price: float) -> bool:
        """Check if stop loss is hit."""
        if self.stop_loss is None:
            return False
        if self.direction == Direction.LONG:
            return current_price <= self.stop_loss
        else:  # SHORT
            return current_price >= self.stop_loss

    def check_target_hit(self, current_price: float) -> bool:
        """Check if take profit is hit."""
        if self.take_profit is None:
            return False
        if self.direction == Direction.LONG:
            return current_price >= self.take_profit
        else:  # SHORT
            return current_price <= self.take_profit

    def calculate_pnl(self, current_price: float) -> tuple[float, float]:
        """Calculate unrealized PnL (absolute and percentage)."""
        if self.direction == Direction.LONG:
            pnl = (current_price - self.entry_price) * self.position_size
        else:  # SHORT
            pnl = (self.entry_price - current_price) * self.position_size
        pnl_pct = (pnl / (self.entry_price * self.position_size)) * 100
        return pnl, pnl_pct

    def update_unrealized(self, current_price: float) -> None:
        """Update unrealized PnL with current price."""
        self.unrealized_pnl, self.unrealized_pnl_pct = self.calculate_pnl(current_price)


@dataclass
class PositionEventRecord:
    """Event record returned by update_positions()."""
    position_id: str
    symbol: str
    event: PositionEvent
    current_price: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Outcome:
    """Outcome record for a closed position."""
    id: int
    decision_id: int
    exit_timestamp: str
    exit_price: float
    pnl_absolute: float
    pnl_pct: float
    hold_duration_minutes: int
    exit_reason: ExitReason
    market_outcome: MarketOutcome
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        """Create Outcome from sqlite3.Row."""
        return cls(
            id=row["id"],
            decision_id=row["decision_id"],
            exit_timestamp=row["exit_timestamp"],
            exit_price=row["exit_price"],
            pnl_absolute=row["pnl_absolute"],
            pnl_pct=row["pnl_pct"],
            hold_duration_minutes=row["hold_duration_minutes"],
            exit_reason=ExitReason(row["exit_reason"]),
            market_outcome=MarketOutcome(row["market_outcome"]),
            created_at=row["created_at"],
        )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)


class PaperTradingEngine:
    """
    Paper trading engine that shadows live signals and tracks virtual positions.
    
    Uses the existing trading.learning.db module for persistence.
    All calculations use stdlib only (no external deps).
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        default_expiry_hours: int = 24,
    ) -> None:
        """
        Initialize the paper trading engine.
        
        Args:
            db_path: Path to SQLite database. Uses default from trading.learning.db if None.
            default_expiry_hours: Default position expiry in hours (default 24).
        """
        self.db_path = db_path
        self.default_expiry_hours = default_expiry_hours
        self._positions: dict[str, Position] = {}  # position_id -> Position
        self._load_open_positions()

    def _load_open_positions(self) -> None:
        """Load open positions from the decisions database."""
        self._positions.clear()
        with get_connection(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM decisions WHERE status = 'OPEN'"
            ).fetchall()
        
        for row in rows:
            decision = Decision.from_row(row)
            position = Position(
                id=str(uuid.uuid4()),  # New UUID for engine tracking
                decision_id=decision.id,
                symbol=decision.symbol,
                direction=decision.direction,
                entry_price=decision.entry_price,
                position_size=decision.position_size,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                entry_time=datetime.fromisoformat(decision.timestamp),
                expiry_hours=self.default_expiry_hours,
                is_open=True,
            )
            self._positions[position.id] = position

    def open_position(self, decision: Decision) -> Position:
        """
        Open a virtual position from a Decision.
        
        Args:
            decision: Decision from the decision journal (must have status OPEN)
            
        Returns:
            Position object with unique ID
        """
        if decision.status != "OPEN":
            raise ValueError(f"Decision {decision.id} is not OPEN (status: {decision.status})")
        
        position = Position(
            id=str(uuid.uuid4()),
            decision_id=decision.id,
            symbol=decision.symbol,
            direction=decision.direction,
            entry_price=decision.entry_price,
            position_size=decision.position_size,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            entry_time=datetime.fromisoformat(decision.timestamp),
            expiry_hours=self.default_expiry_hours,
            is_open=True,
        )
        self._positions[position.id] = position
        return position

    def update_positions(
        self,
        prices: dict[str, float],
        now: Optional[datetime] = None
    ) -> list[PositionEventRecord]:
        """
        Update all open positions with current market prices.
        
        Checks each open position against stop_loss, take_profit, and expiry.
        Auto-closes positions that hit stops/targets or expire.
        
        Args:
            prices: Dict mapping symbol -> current_price
            now: Current time (defaults to utcnow)
            
        Returns:
            List of PositionEventRecord for each position that had an event
        """
        if now is None:
            now = datetime.utcnow()
        
        events: list[PositionEventRecord] = []
        to_close: list[tuple[str, PositionEvent, float]] = []  # (position_id, event, price)
        
        for position_id, position in self._positions.items():
            if not position.is_open:
                continue
            
            current_price = prices.get(position.symbol)
            if current_price is None:
                # No price update for this symbol, check expiry only
                if position.is_expired(now):
                    to_close.append((position_id, PositionEvent.TIME_EXPIRED, position.entry_price))
                continue
            
            # Update unrealized PnL
            position.update_unrealized(current_price)
            
            # Check stop loss
            if position.check_stop_hit(current_price):
                to_close.append((position_id, PositionEvent.STOP_HIT, current_price))
                continue
            
            # Check take profit
            if position.check_target_hit(current_price):
                to_close.append((position_id, PositionEvent.TARGET_HIT, current_price))
                continue
            
            # Check expiry
            if position.is_expired(now):
                to_close.append((position_id, PositionEvent.TIME_EXPIRED, current_price))
                continue
            
            # No event
            events.append(PositionEventRecord(
                position_id=position_id,
                symbol=position.symbol,
                event=PositionEvent.NONE,
                current_price=current_price,
                timestamp=now,
            ))
        
        # Close positions that hit stops/targets/expired
        for position_id, event, exit_price in to_close:
            position = self._positions[position_id]
            # Don't mark closed here - let process_events_and_close handle it
            events.append(PositionEventRecord(
                position_id=position_id,
                symbol=position.symbol,
                event=event,
                current_price=exit_price,
                timestamp=now,
            ))
        
        return events

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: ExitReason = ExitReason.MANUAL,
        now: Optional[datetime] = None
    ) -> Outcome:
        """
        Close a position and record the outcome.
        
        Args:
            position_id: Position UUID
            exit_price: Exit price
            exit_reason: Reason for exit
            now: Exit timestamp (defaults to utcnow)
            
        Returns:
            Outcome dataclass with PnL calculations
        """
        if position_id not in self._positions:
            raise ValueError(f"Position {position_id} not found")
        
        position = self._positions[position_id]
        if not position.is_open:
            raise ValueError(f"Position {position_id} is already closed")
        
        if now is None:
            now = datetime.utcnow()
        
        # Calculate PnL
        if position.direction == Direction.LONG:
            pnl_absolute = (exit_price - position.entry_price) * position.position_size
        else:  # SHORT
            pnl_absolute = (position.entry_price - exit_price) * position.position_size
        
        pnl_pct = (pnl_absolute / (position.entry_price * position.position_size)) * 100
        
        # Calculate hold duration
        entry_dt = position.entry_time
        hold_duration_minutes = int((now - entry_dt).total_seconds() / 60)
        
        # Determine market outcome
        if exit_price > position.entry_price:
            market_outcome = MarketOutcome.UP
        elif exit_price < position.entry_price:
            market_outcome = MarketOutcome.DOWN
        else:
            market_outcome = MarketOutcome.SIDEWAYS
        
        # Map exit_reason to db enum
        exit_reason_db = exit_reason.value
        
        # Create outcome in database
        outcome_id = add_outcome(
            decision_id=position.decision_id,
            exit_timestamp=now.isoformat(),
            exit_price=exit_price,
            pnl_absolute=pnl_absolute,
            pnl_pct=pnl_pct,
            hold_duration_minutes=hold_duration_minutes,
            exit_reason=exit_reason_db,
            market_outcome=market_outcome.value,
            db_path=self.db_path,
        )
        
        # Update decision status
        update_decision_status(position.decision_id, "CLOSED", self.db_path)
        
        # Mark position closed
        position.is_open = False
        
        # Return Outcome object
        return Outcome(
            id=outcome_id,
            decision_id=position.decision_id,
            exit_timestamp=now.isoformat(),
            exit_price=exit_price,
            pnl_absolute=pnl_absolute,
            pnl_pct=pnl_pct,
            hold_duration_minutes=hold_duration_minutes,
            exit_reason=exit_reason,
            market_outcome=market_outcome,
            created_at=datetime.utcnow().isoformat(),
        )

    def get_open_positions(self) -> list[Position]:
        """Get all currently open virtual positions."""
        return [p for p in self._positions.values() if p.is_open]

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get a position by ID."""
        return self._positions.get(position_id)

    def get_summary(self) -> dict:
        """
        Get portfolio summary statistics.
        
        Returns:
            Dict with total_pnl, realized_pnl, unrealized_pnl, open_positions_count,
            win_count, loss_count, win_rate
        """
        open_positions = self.get_open_positions()
        
        # Calculate unrealized PnL from open positions
        unrealized_pnl = sum(p.unrealized_pnl for p in open_positions)
        
        # Get realized PnL from outcomes table
        realized_pnl = 0.0
        win_count = 0
        loss_count = 0
        
        with get_connection(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT pnl_absolute FROM outcomes"
            ).fetchall()
            
            for row in rows:
                pnl = row["pnl_absolute"]
                realized_pnl += pnl
                if pnl > 0:
                    win_count += 1
                elif pnl < 0:
                    loss_count += 1
        
        total_pnl = realized_pnl + unrealized_pnl
        total_trades = win_count + loss_count
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0
        
        return {
            "total_pnl": round(total_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "open_positions_count": len(open_positions),
            "win_count": win_count,
            "loss_count": loss_count,
            "total_closed_trades": total_trades,
            "win_rate": round(win_rate, 2),
        }

    def process_events_and_close(
        self,
        prices: dict[str, float],
        now: Optional[datetime] = None
    ) -> list[Outcome]:
        """
        Convenience method: update positions and auto-close those with events.
        
        Args:
            prices: Dict mapping symbol -> current_price
            now: Current time
            
        Returns:
            List of Outcome objects for positions that were closed
        """
        events = self.update_positions(prices, now)
        outcomes: list[Outcome] = []
        
        for event in events:
            if event.event in (PositionEvent.STOP_HIT, PositionEvent.TARGET_HIT, PositionEvent.TIME_EXPIRED):
                position = self._positions[event.position_id]
                if not position.is_open:
                    # Already closed by update_positions, get outcome from db
                    outcome_row = get_outcome(position.decision_id, self.db_path)
                    if outcome_row:
                        outcomes.append(Outcome.from_row(outcome_row))
                else:
                    # Close it now
                    exit_reason_map = {
                        PositionEvent.STOP_HIT: ExitReason.STOP_LOSS,
                        PositionEvent.TARGET_HIT: ExitReason.TAKE_PROFIT,
                        PositionEvent.TIME_EXPIRED: ExitReason.TIME_EXPIRY,
                    }
                    outcome = self.close_position(
                        position_id=event.position_id,
                        exit_price=event.current_price,
                        exit_reason=exit_reason_map[event.event],
                        now=now,
                    )
                    outcomes.append(outcome)
        
        return outcomes


def create_engine(
    db_path: Optional[Path] = None,
    default_expiry_hours: int = 24,
) -> PaperTradingEngine:
    """Factory function to create a PaperTradingEngine."""
    return PaperTradingEngine(db_path=db_path, default_expiry_hours=default_expiry_hours)


if __name__ == "__main__":
    # Quick test
    from trading.learning.db import init_db, add_decision
    
    init_db()
    
    # Add a test decision
    decision_id = add_decision(
        timestamp=datetime.utcnow().isoformat(),
        symbol="RELIANCE",
        signal_source="momentum",
        signal_strength=85,
        direction="LONG",
        entry_price=2500.0,
        position_size=10,
        stop_loss=2450.0,
        take_profit=2600.0,
        confidence=90,
        reasoning="Strong momentum breakout",
        rule_version=1,
    )
    
    engine = create_engine()
    
    # Get the decision
    from trading.learning.db import get_decision
    decision = get_decision(decision_id)
    
    # Open position
    position = engine.open_position(decision)
    print(f"Opened position: {position.id} for {position.symbol} {position.direction}")
    
    # Update with price moving to stop loss
    events = engine.update_positions({"RELIANCE": 2440.0})
    print(f"Events: {events}")
    
    # Process and close
    outcomes = engine.process_events_and_close({"RELIANCE": 2440.0})
    for outcome in outcomes:
        print(f"Closed: PnL={outcome.pnl_absolute:.2f} ({outcome.pnl_pct:.2f}%) Reason={outcome.exit_reason.value}")
    
    # Summary
    summary = engine.get_summary()
    print(f"Summary: {summary}")