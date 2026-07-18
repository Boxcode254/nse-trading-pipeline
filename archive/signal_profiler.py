"""Signal Profiler - scores signal sources for copyability/quality (like wallet profiler)."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from trading.learning.db import get_connection


@dataclass
class SignalMetrics:
    """Quality metrics for a signal source."""
    signal_source: str
    lookback_days: int
    total_signals: int
    executed_signals: int
    win_rate: float
    avg_pnl_pct: float
    avg_hold_minutes: float
    max_drawdown_pct: float
    consistency_score: float
    sizing_discipline: float
    sharpe_like: Optional[float]
    last_signal_timestamp: Optional[str]


class SignalProfiler:
    """Profiles signal sources from the decision journal."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path

    def _get_decisions_with_outcomes(
        self,
        signal_source: Optional[str] = None,
        lookback_days: int = 30
    ) -> list[dict]:
        """Get decisions joined with outcomes for a signal source."""
        cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()

        with get_connection(self.db_path) as conn:
            conn.row_factory = None  # We'll use dicts
            cursor = conn.cursor()

            if signal_source:
                cursor.execute(
                    """
                    SELECT d.*, o.pnl_absolute, o.pnl_pct, o.hold_duration_minutes,
                           o.exit_reason, o.market_outcome, o.exit_timestamp
                    FROM decisions d
                    LEFT JOIN outcomes o ON d.id = o.decision_id
                    WHERE d.signal_source = ? AND d.timestamp >= ?
                    ORDER BY d.timestamp ASC
                    """,
                    (signal_source, cutoff)
                )
            else:
                cursor.execute(
                    """
                    SELECT d.*, o.pnl_absolute, o.pnl_pct, o.hold_duration_minutes,
                           o.exit_reason, o.market_outcome, o.exit_timestamp
                    FROM decisions d
                    LEFT JOIN outcomes o ON d.id = o.decision_id
                    WHERE d.timestamp >= ?
                    ORDER BY d.timestamp ASC
                    """,
                    (cutoff,)
                )

            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "symbol": r[2],
                    "signal_source": r[3],
                    "signal_strength": r[4],
                    "direction": r[5],
                    "entry_price": r[6],
                    "position_size": r[7],
                    "stop_loss": r[8],
                    "take_profit": r[9],
                    "confidence": r[10],
                    "reasoning": r[11],
                    "rule_version": r[12],
                    "status": r[13],
                    "created_at": r[14],
                    "pnl_absolute": r[15],
                    "pnl_pct": r[16],
                    "hold_duration_minutes": r[17],
                    "exit_reason": r[18],
                    "market_outcome": r[19],
                    "exit_timestamp": r[20],
                }
                for r in rows
            ]

    def _get_all_signal_sources(self, lookback_days: int = 30) -> list[str]:
        """Get all unique signal sources with decisions in lookback."""
        cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()

        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT signal_source FROM decisions WHERE timestamp >= ?",
                (cutoff,)
            )
            return [r[0] for r in cursor.fetchall()]

    def _calculate_max_drawdown(self, pnl_series: list[float]) -> float:
        """Calculate max drawdown from equity curve (cumulative PnL)."""
        if not pnl_series:
            return 0.0

        equity = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in pnl_series:
            equity += pnl
            if equity > peak:
                peak = equity
            elif peak > 0:
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd

        return max_dd

    def _calculate_sizing_discipline(self, decisions: list[dict]) -> float:
        """Score how well position sizes follow Kelly/optimal (0-100)."""
        if not decisions:
            return 0.0

        # Simple heuristic: check if sizes are consistent and reasonable
        sizes = [d["position_size"] for d in decisions if d["position_size"] > 0]
        if not sizes:
            return 0.0

        # Coefficient of variation (lower = more disciplined)
        mean_size = statistics.mean(sizes)
        if mean_size == 0:
            return 0.0

        if len(sizes) == 1:
            return 50.0  # Neutral for single signal

        cv = statistics.stdev(sizes) / mean_size
        # Score: 100 at CV=0, 0 at CV=1+
        return max(0.0, 100.0 * (1.0 - min(cv, 1.0)))

    def calculate_signal_metrics(
        self,
        signal_source: str,
        lookback_days: int = 30
    ) -> SignalMetrics:
        """Calculate quality metrics for a single signal source."""
        decisions = self._get_decisions_with_outcomes(signal_source, lookback_days)

        if not decisions:
            return SignalMetrics(
                signal_source=signal_source,
                lookback_days=lookback_days,
                total_signals=0,
                executed_signals=0,
                win_rate=0.0,
                avg_pnl_pct=0.0,
                avg_hold_minutes=0.0,
                max_drawdown_pct=0.0,
                consistency_score=0.0,
                sizing_discipline=0.0,
                sharpe_like=None,
                last_signal_timestamp=None,
            )

        total_signals = len(decisions)
        closed = [d for d in decisions if d["pnl_absolute"] is not None]
        executed_signals = len(closed)

        if executed_signals == 0:
            return SignalMetrics(
                signal_source=signal_source,
                lookback_days=lookback_days,
                total_signals=total_signals,
                executed_signals=0,
                win_rate=0.0,
                avg_pnl_pct=0.0,
                avg_hold_minutes=0.0,
                max_drawdown_pct=0.0,
                consistency_score=0.0,
                sizing_discipline=self._calculate_sizing_discipline(decisions),
                sharpe_like=None,
                last_signal_timestamp=decisions[-1]["timestamp"] if decisions else None,
            )

        # Win rate
        wins = [d for d in closed if d["pnl_absolute"] > 0]
        win_rate = len(wins) / executed_signals * 100

        # Avg PnL %
        avg_pnl_pct = statistics.mean(d["pnl_pct"] for d in closed)

        # Avg hold time
        holds = [d["hold_duration_minutes"] for d in closed if d["hold_duration_minutes"] is not None]
        avg_hold = statistics.mean(holds) if holds else 0.0

        # Max drawdown from equity curve
        pnl_series = [d["pnl_absolute"] for d in closed]
        max_dd = self._calculate_max_drawdown(pnl_series)

        # Sizing discipline
        sizing_disc = self._calculate_sizing_discipline(decisions)

        # Sharpe-like (avg/std)
        sharpe = None
        if len(pnl_series) > 1:
            std_pnl = statistics.stdev(pnl_series)
            if std_pnl > 0:
                sharpe = avg_pnl_pct / std_pnl

        # Consistency score: win_rate * (1 - max_dd/100) * frequency_factor
        freq_factor = min(1.0, executed_signals / 20.0)  # Normalize to 20 signals
        consistency = win_rate * (1.0 - max_dd / 100.0) * freq_factor

        return SignalMetrics(
            signal_source=signal_source,
            lookback_days=lookback_days,
            total_signals=total_signals,
            executed_signals=executed_signals,
            win_rate=round(win_rate, 2),
            avg_pnl_pct=round(avg_pnl_pct, 2),
            avg_hold_minutes=round(avg_hold, 1),
            max_drawdown_pct=round(max_dd, 2),
            consistency_score=round(consistency, 2),
            sizing_discipline=round(sizing_disc, 2),
            sharpe_like=round(sharpe, 2) if sharpe else None,
            last_signal_timestamp=decisions[-1]["timestamp"] if decisions else None,
        )

    def get_all_signal_metrics(self, lookback_days: int = 30) -> list[SignalMetrics]:
        """Get metrics for all signal sources, sorted by consistency_score desc."""
        sources = self._get_all_signal_sources(lookback_days)
        metrics = [
            self.calculate_signal_metrics(src, lookback_days)
            for src in sources
        ]
        return sorted(metrics, key=lambda m: m.consistency_score, reverse=True)

    def filter_copyable_signals(
        self,
        min_win_rate: float = 50.0,
        max_drawdown: float = 20.0,
        min_signals: int = 10,
        lookback_days: int = 30
    ) -> list[SignalMetrics]:
        """Return signals meeting copyability thresholds."""
        all_metrics = self.get_all_signal_metrics(lookback_days)
        return [
            m for m in all_metrics
            if m.executed_signals >= min_signals
            and m.win_rate >= min_win_rate
            and m.max_drawdown_pct <= max_drawdown
        ]

    def get_signal_rankings(self, lookback_days: int = 30) -> dict:
        """Get ranked signal categories."""
        all_metrics = self.get_all_signal_metrics(lookback_days)

        top = [m for m in all_metrics if m.consistency_score > 20 and m.executed_signals >= 10]
        avoid = [m for m in all_metrics if m.win_rate < 40 and m.executed_signals >= 5]
        needs_data = [m for m in all_metrics if m.executed_signals < 5]

        return {
            "top_performers": [
                {"signal": m.signal_source, "score": m.consistency_score, "win_rate": m.win_rate}
                for m in top[:5]
            ],
            "avoid_list": [
                {"signal": m.signal_source, "win_rate": m.win_rate, "max_dd": m.max_drawdown_pct}
                for m in avoid[:5]
            ],
            "needs_more_data": [m.signal_source for m in needs_data],
            "total_sources": len(all_metrics),
        }


def create_profiler(db_path: Optional[Path] = None) -> SignalProfiler:
    """Factory function to create a SignalProfiler."""
    return SignalProfiler(db_path=db_path)


if __name__ == "__main__":
    # Quick test
    profiler = create_profiler()
    metrics = profiler.get_all_signal_metrics()
    for m in metrics:
        print(f"{m.signal_source}: score={m.consistency_score}, wr={m.win_rate}%, dd={m.max_drawdown_pct}%")

    print("\nCopyable:")
    for m in profiler.filter_copyable_signals():
        print(f"  {m.signal_source}: score={m.consistency_score}")

    print("\nRankings:")
    rankings = profiler.get_signal_rankings()
    print(f"  Top: {rankings['top_performers']}")
    print(f"  Avoid: {rankings['avoid_list']}")
    print(f"  Needs data: {rankings['needs_more_data']}")