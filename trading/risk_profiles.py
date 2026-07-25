"""Fail-open per-stock risk profiles: volatility, liquidity, correlation.

These helpers feed the allocation engine's position-sizing logic. They are
deliberately pure and never raise: when rich inputs (price history, a realized
correlation matrix) are unavailable, every function degrades gracefully to the
ranking factor scores already produced by the signal service, so the live
rebalance path stays offline and deterministic.

Source hierarchy for each input:
  * richest  : local price-history cache (realized vol / realized corr matrix)
  * fallback : ranking `volatility` / `liquidity` factor scores (0..100)
  * floor    : a neutral default (never crashes the caller)
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from trading import config

# Pull the canonical sector map so correlation assumptions stay in sync with
# the rest of the engine (one source of truth, no drift).
SECTOR_OF = dict(config.SECTOR_MAP)


def _signal_lookup(symbol: str, signals: Optional[dict]) -> dict:
    if not signals:
        return {}
    return signals.get(symbol, {}) or {}


def liquidity_score(symbol: str, signals: Optional[dict] = None) -> float:
    """Return a 0..1 liquidity proxy for ``symbol``.

    Prefers the ranking ``liquidity`` factor (0..100). Falls back to a neutral
    0.5 when absent or unparseable so sizing never divides by zero or breaks.
    """
    sig = _signal_lookup(symbol, signals)
    liq = sig.get("liquidity")
    if liq is None:
        return 0.5
    try:
        v = float(liq)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v / 100.0))


def realized_vol(
    symbol: str,
    history: Optional[Any] = None,
    signal_vol: float = 0.5,
) -> float:
    """Annualised volatility normalised to 0..1.

    If ``history`` (array-like of closes) has >= 3 points, compute the
    realized annualised stdev of daily returns, normalised so ~40% annualised
    vol maps to 1.0. Otherwise fall back to ``signal_vol`` (already 0..1).
    """
    if history is not None:
        try:
            arr = np.asarray(history, dtype=float)
            if arr.size >= 3:
                rets = np.diff(arr) / arr[:-1]
                ann = float(np.std(rets) * np.sqrt(252))
                return max(0.0, min(1.0, ann / 0.40))
        except Exception:
            pass
    return max(0.0, min(1.0, float(signal_vol)))


def pairwise_corr(a: str, b: str, matrix: Optional[dict] = None) -> float:
    """Pairwise correlation of two symbols in 0..1 (absolute, for risk)."""
    if matrix and a in matrix:
        inner = matrix.get(a, {})
        if b in inner:
            try:
                return max(0.0, min(1.0, abs(float(inner[b]))))
            except (TypeError, ValueError):
                pass
    # Static NSE assumption: names in the same sector move together; banks most.
    sa = SECTOR_OF.get(a)
    sb = SECTOR_OF.get(b)
    if sa == sb == "banking":
        return 0.85
    if sa == sb and sa is not None:
        return 0.55
    return 0.15


def corr_penalty(
    symbol: str,
    peers: list[str],
    matrix: Optional[dict] = None,
) -> float:
    """Sum of ``symbol``'s correlations to its ``peers`` (0..n)."""
    return float(
        sum(pairwise_corr(symbol, p, matrix) for p in peers if p != symbol)
    )


def risk_weight(
    symbol: str,
    *,
    vol: float,
    liq: float,
    corr_penalty_norm: float,
) -> float:
    """Raw risk-driven weight for one name.

    Low vol → higher weight. Liquidity scales linearly. Correlation trims:
    a name that is highly correlated with its sector peers adds less
    diversification benefit, so it receives a smaller slice.

    ``corr_penalty_norm`` is the correlation penalty already normalised to
    0..1 (e.g. ``corr_penalty / max(1, len(peers))``).
    """
    inv_vol = 1.0 / max(vol, 0.05)
    liquidity_factor = 0.5 + 0.5 * max(0.0, min(1.0, liq))
    corr_factor = 1.0 - 0.5 * max(0.0, min(1.0, corr_penalty_norm))
    return max(inv_vol * liquidity_factor * corr_factor, 1e-6)
