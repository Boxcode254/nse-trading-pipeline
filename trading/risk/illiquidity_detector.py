"""Dynamic illiquidity / suspension detector.

WHY THIS EXISTS
===============
The live allocation engine relies entirely on the STATIC
``config.SUSPENDED_SYMBOLS`` list (BAMB only) to avoid trading — or worse,
*holding* — a suspended / frozen counter. Any NSE name that gets suspended
without being pre-listed would be held **silently and permanently**: the
strategy reports it as a normal healthy holding, never flags it, never exits.
That is a structurally dangerous gap for real money — it fails silently and
forever, unlike every other bug this cycle which at least announced itself.

This module detects the condition *from price/volume data*, so the next
non-BAMB suspension is caught automatically.

THE SIGNATURE (calibrated against cached NSE history)
====================================================
True zero-volume bars do NOT exist in the cached NSE data — every name
reports *some* volume (thin names like WTK at 52 shares, TOTL at 100). A
"zero-volume" detector would never fire and be dead code.

The real suspension signature in the cache is an **OHLC lock**:
``open == high == low == close`` for an extended run — the counter is
pinned at one price with no intraday range. BAMB froze at 54.0 (vol pinned
at 1500) from 2025-05-14 onward; its max consecutive O=H=L=C run is 27 bars.
Across the full 12-name universe, the longest NON-suspended lock run is
WTK at **7 bars** (naturally thin, but still has real intraday range on
other days). So a threshold of ``>= 10`` consecutive locked bars:
  * fires on BAMB (27)              ✓ true positive
  * fires on ZERO other names (max 7) ✓ no false positives
  * leaves natural illiquidity (WTK=7) safely below threshold

We also raise a *soft* warning at a shorter window (default 5) so a name
trending toward a lock is surfaced for human review before it hard-locks.

DESIGN
======
- Pure function over a close/OHLC/volume series — no network, no state.
- Returns a structured verdict (status, since_date, run_length, confidence)
  so callers can alert, soft-exclude from buys, or hard-flag a hold.
- Does NOT auto-sell (that's a policy decision for the auto-trader / human);
  it only DETECTS and reports. The auto-trader decides what to do with it.
"""
from __future__ import annotations

import pandas as pd
from dataclasses import dataclass
from typing import Optional

# ── Tunable thresholds (calibrated, see module docstring) ──
HARD_LOCK_BARS = 10   # >= this many consecutive O=H=L=C bars => SUSPENDED/LOCKED
SOFT_LOCK_BARS = 5    # >= this many => SUSPICION (trending toward lock)


@dataclass
class IlliquidityVerdict:
    symbol: str
    status: str          # "healthy" | "suspicious" | "locked"
    run_length: int      # current consecutive locked-bar count
    since_date: Optional[str]   # first date of the current locked run
    confidence: float    # 0..1 — fraction of the run that is a clean OHLC lock
    note: str


def _ohlc_locked(row: pd.Series) -> bool:
    """True if this bar is fully locked: open==high==low==close."""
    try:
        o = float(row["open"]); h = float(row["high"])
        l = float(row["low"]); c = float(row["close"])
    except (TypeError, ValueError):
        return False
    if h == 0 and l == 0 and o == 0 and c == 0:
        return False  # all-zero bar is missing data, not a lock
    return o == h == l == c


def detect_illiquidity(
    symbol: str,
    df: pd.DataFrame,
    hard_bars: int = HARD_LOCK_BARS,
    soft_bars: int = SOFT_LOCK_BARS,
) -> IlliquidityVerdict:
    """Scan a symbol's bar history for an active OHLC-lock condition.

    Args:
        symbol: ticker for the report.
        df: DataFrame with columns [date, open, high, low, close, (volume)].
        hard_bars / soft_bars: thresholds (see module docstring).

    Returns the verdict for the MOST RECENT locked run (the one that matters
    for live trading). If the latest bar is not locked, status is "healthy"
    regardless of historical locks (those already resolved).
    """
    if df is None or len(df) == 0:
        return IlliquidityVerdict(symbol, "healthy", 0, None, 0.0,
                                  "no data")

    d = df.copy()
    d.columns = [c.lower() for c in d.columns]
    if "date" in d.columns:
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.sort_values("date").reset_index(drop=True)

    locked = d.apply(_ohlc_locked, axis=1).astype(bool).values

    # Walk from the end to find the current trailing lock run.
    run = 0
    start_idx = None
    for i in range(len(locked) - 1, -1, -1):
        if locked[i]:
            if run == 0:
                start_idx = i
            run += 1
        else:
            break

    if run == 0:
        return IlliquidityVerdict(symbol, "healthy", 0, None, 0.0,
                                  "no active lock")

    since = str(d["date"].iloc[start_idx].date()) if "date" in d.columns else None
    # confidence: all bars in the run are clean OHLC locks (they are, by
    # construction). We keep the field for future volume-weighted tuning.
    confidence = 1.0

    if run >= hard_bars:
        return IlliquidityVerdict(
            symbol, "locked", run, since, confidence,
            f"OHLC locked (O=H=L=C) for {run} consecutive bars since {since} "
            f"— consistent with suspension/halt. Treat as non-tradeable.",
        )
    if run >= soft_bars:
        return IlliquidityVerdict(
            symbol, "suspicious", run, since, confidence,
            f"OHLC locked for {run} consecutive bars since {since} — trending "
            f"toward a full lock; monitor for suspension.",
        )
    return IlliquidityVerdict(
        symbol, "healthy", run, since, confidence,
        f"brief {run}-bar lock, below suspicion threshold ({soft_bars}).",
    )


def scan_universe(
    bars: dict[str, pd.DataFrame],
    hard_bars: int = HARD_LOCK_BARS,
    soft_bars: int = SOFT_LOCK_BARS,
) -> dict[str, IlliquidityVerdict]:
    """Run detect_illiquidity over a {symbol: df} map. Returns verdicts keyed
    by symbol. Only non-'healthy' ones are interesting, but all are returned."""
    out: dict[str, IlliquidityVerdict] = {}
    for sym, df in bars.items():
        out[sym] = detect_illiquidity(sym, df, hard_bars, soft_bars)
    return out


def flagged(verdict: IlliquidityVerdict) -> bool:
    """True if the verdict warrants action (suspicious or locked)."""
    return verdict.status in ("suspicious", "locked")
