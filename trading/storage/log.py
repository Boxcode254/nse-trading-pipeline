"""Append-only CSV log of generated signals + learning database for recommendations.

Schema (signals.csv)
--------------------
timestamp, pair, signal, price, sma_fast, sma_slow, rsi

Why CSV: the spec is explicit that there's no database for signals, and a flat file
keeps the package easy to inspect with `tail`, `awk`, or pandas.

Learning Database (learning.db)
--------------------------------
SQLite database at ~/.trading/learning/learning.db tracking recommendations
with confidence, score, factors, and market outcomes for performance analysis.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Optional

from .. import config

CSV_COLUMNS = ["timestamp", "pair", "signal", "price", "sma_fast", "sma_slow", "rsi"]


def _round(value: Any, digits: int) -> str:
    """Round a numeric value to ``digits`` for compact CSV output.

    Non-numeric values (including empty strings) are passed through unchanged.
    NaN becomes an empty cell.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v != v:  # NaN
        return ""
    return f"{v:.{digits}f}"


def _now_iso() -> str:
    """ISO-8601 timestamp, second precision, local-timezone naive."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _date_iso() -> str:
    """ISO-8601 date (YYYY-MM-DD)."""
    return datetime.now().strftime("%Y-%m-%d")


def log_signal(signal: dict[str, Any], timestamp: Optional[str] = None) -> None:
    """Append a single signal row to signals.csv. Creates the file with a header
    on first write."""
    config.ensure_dirs()
    path = config.SIGNALS_CSV
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0

    row = {
        "timestamp": timestamp or _now_iso(),
        "pair": signal.get("pair", ""),
        "signal": signal.get("signal", ""),
        "price": _round(signal.get("price", ""), 6),
        "sma_fast": _round(signal.get("sma_fast", ""), 6),
        "sma_slow": _round(signal.get("sma_slow", ""), 6),
        "rsi": _round(signal.get("rsi", ""), 4),
    }

    # Use 'a' so we never clobber history; newline='' keeps csv.writer happy on
    # platforms that double-space lines by default.
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def read_history(n: int = 20) -> list[dict[str, Any]]:
    """Return up to the last ``n`` signal rows (most recent first)."""
    path = config.SIGNALS_CSV
    if not os.path.exists(path):
        return []
    with open(path, "r", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:][::-1]


# ── Learning Log ──────────────────────────────────────────────────────────
# Maps the 5-tier recommendation labels to the 3-class learning DB schema.

_RECOMMENDATION_TO_ACTION = {
    config.TIER_STRONG_ACCUMULATE: "BUY",
    config.TIER_ACCUMULATE: "BUY",
    config.TIER_HOLD: "HOLD",
    config.TIER_REDUCE: "SELL",
    config.TIER_AVOID: "SELL",
}


def _map_recommendation(tier: str) -> str:
    """Map 5-tier recommendation to 3-class action for learning DB."""
    return _RECOMMENDATION_TO_ACTION.get(tier, "HOLD")


def _ensure_learning_db():
    """Lazy import and initialization of the learning database."""
    import sys
    import os
    # Add the learning module path
    learning_path = os.path.join(os.path.dirname(__file__), "..", "..", "learning")
    learning_path = os.path.abspath(learning_path)
    if learning_path not in sys.path:
        sys.path.insert(0, learning_path)
    from db import LearningDB, Recommendation
    return LearningDB, Recommendation


def learning_log(
    symbol: str,
    recommendation: str,
    confidence: float,
    score: float,
    factors: dict[str, Any],
    date: Optional[str] = None,
) -> int:
    """Log a recommendation to the learning database for tracking and analysis.

    Args:
        symbol: Trading symbol (e.g., "SCOM", "EUR/USD")
        recommendation: One of config.TIER_STRONG_ACCUMULATE, TIER_ACCUMULATE,
            TIER_HOLD, TIER_REDUCE, TIER_AVOID
        confidence: Confidence as a fraction 0.0-1.0 (e.g., 0.75 for 75%)
        score: Composite score 0-100
        factors: Dict of factor scores used in the recommendation
        date: Optional date string YYYY-MM-DD (defaults to today)

    Returns:
        The recommendation ID from the learning database.

    The function maps the 5-tier recommendation to BUY/SELL/HOLD for the
    learning database schema and stores the factors as a JSON hash for
    deduplication.
    """
    LearningDB, Recommendation = _ensure_learning_db()
    db = LearningDB()

    rec_date = date or _date_iso()
    action = _map_recommendation(recommendation)

    rec = Recommendation(
        symbol=symbol.upper(),
        date=rec_date,
        confidence=max(0.0, min(1.0, float(confidence))),
        recommendation=action,
        score=max(0.0, min(100.0, float(score))),
        factors=factors,
    )

    rec_id = db.add_recommendation(rec)
    return rec_id


def learning_log_from_signal(
    symbol: str,
    signal_data: dict[str, Any],
    score: float,
    factors: dict[str, float],
    recommendation: Optional[str] = None,
    date: Optional[str] = None,
) -> int:
    """Convenience wrapper that logs from a signal dict + ranking output.

    Args:
        symbol: Trading symbol
        signal_data: Dict from signal_for_symbol() or explain_symbol()
        score: Composite score from ranker (0-100)
        factors: Factor scores dict from scorer.score_all_factors()
        recommendation: Optional override; if None, derived from score
        date: Optional date YYYY-MM-DD (defaults to today)

    Returns:
        The recommendation ID from the learning database.
    """
    from ..ranking import ranker

    if recommendation is None:
        recommendation = ranker.recommendation_for(score)

    confidence = signal_data.get("confidence", 0.0)

    return learning_log(
        symbol=symbol,
        recommendation=recommendation,
        confidence=confidence,
        score=score,
        factors=factors,
        date=date,
    )
