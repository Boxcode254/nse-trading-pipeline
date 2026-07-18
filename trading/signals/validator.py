"""Signal Validator — deterministic filters that reject weak signals.

Sits between the signal engine and logging/reporting:

    engine.generate_signals() -> validator.filter() -> log + report

Every filter is a pure function — no state, no I/O. The validator returns
(accepted, rejected) lists so the caller can log both for audit.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .. import config

# Exported filter registry — used by filter() and tests
VALIDATION_FILTERS: list[str] = [
    "confidence_threshold",
    "volume_filter",
    "duplicate_filter",
    "spread_filter",
]


# ── Confidence score ─────────────────────────────────────────────────


def calculate_confidence(signal: dict[str, Any]) -> float:
    """Return a confidence score 0–100 for any signal.

    Formula
    -------
    How far RSI is from the neutral 50 mark, scaled so RSI=100 → 100,
    RSI=0 → 100, RSI=50 → 0.

        confidence = min(100, abs(rsi - 50) * 2)

    Returns 0.0 if RSI is not available.
    """
    rsi = signal.get("rsi")
    if rsi is None or pd.isna(rsi):
        return 0.0
    return min(100.0, abs(float(rsi) - 50.0) * 2.0)


def _check_confidence(signal: dict[str, Any], _df: pd.DataFrame) -> str | None:
    """Reject if RSI is too close to 50 (weak conviction).

    A BUY with RSI between 50 and CONFIDENCE_MIN_RSI_DELTA is flagged as
    low-confidence. A SELL with RSI between 50 and 50 - CONFIDENCE_MIN_RSI_DELTA
    is similarly flagged.
    """
    rsi = signal.get("rsi")
    if rsi is None or pd.isna(rsi):
        return None  # can't judge, let it through
    delta = config.CONFIDENCE_MIN_RSI_DELTA
    if signal["signal"] == "BUY" and rsi < 50.0 + delta:
        return f"rsi={rsi:.1f} too close to 50 (needs >{50+delta:.0f} for BUY)"
    if signal["signal"] == "SELL" and rsi > 50.0 - delta:
        return f"rsi={rsi:.1f} too close to 50 (needs <{50-delta:.0f} for SELL)"
    return None


def _check_spread(signal: dict[str, Any], df: pd.DataFrame) -> str | None:
    """Reject if today's high-low spread exceeds CONFIRM_MAX_SPREAD_FRAC * close."""
    if df.empty or "high" not in df.columns or "low" not in df.columns:
        return None
    close = signal.get("price")
    if close is None or close == 0 or pd.isna(close):
        return None
    spread = df["high"].iloc[-1] - df["low"].iloc[-1]
    if spread > config.CONFIRM_MAX_SPREAD_FRAC * close:
        return (
            f"spread={spread:.5f} exceeds "
            f"{config.CONFIRM_MAX_SPREAD_FRAC*100:.1f}% of price"
        )
    return None


def _check_volume(signal: dict[str, Any], df: pd.DataFrame) -> str | None:
    """Reject if volume is below the asset-class threshold.

    Looks up the per-asset-class ``min_volume`` from ``config.ASSET_FILTERS``.
    Falls back to 0 (no filter) if the asset class isn't configured.
    """
    if "volume" not in df.columns or df["volume"].empty:
        return None
    vol = float(df["volume"].iloc[-1])

    asset_class = signal.get("asset", "stocks")
    thresholds = config.ASSET_FILTERS.get(asset_class, {})
    min_vol = thresholds.get("min_volume", 0.0)

    if vol < min_vol:
        return f"volume={vol:.0f} below {asset_class} minimum {min_vol:,.0f}"
    return None


def _check_duplicate(
    signal: dict[str, Any],
    _df: pd.DataFrame,
    *,
    _history: list[dict[str, Any]] | None = None,
) -> str | None:
    """Reject if an identical signal was logged within DUPLICATE_COOLDOWN_HOURS."""
    hist = _history if _history is not None else []
    if not hist:
        return None
    from ..storage import log as storage_log

    pair = signal.get("pair", "")
    verdict = signal.get("signal", "")
    if verdict == "HOLD":
        return None  # HOLD doesn't need dedup

    # Pull recent history from storage
    try:
        recent = storage_log.read_history(50)
    except Exception:
        return None  # on error, let it through

    import datetime

    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(hours=config.DUPLICATE_COOLDOWN_HOURS)

    for row in recent:
        ts_str = row.get("timestamp", "")
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        if row.get("pair") == pair and row.get("signal") == verdict:
            return (
                f"duplicate {verdict} on {pair} within "
                f"last {config.DUPLICATE_COOLDOWN_HOURS}h "
                f"(last was {ts_str})"
            )
    return None


# ── Filter registry ──────────────────────────────────────────────────


def _all_filters() -> list[dict]:
    """Return the ordered list of filters with their metadata."""
    return [
        {
            "name": "confidence_threshold",
            "fn": _check_confidence,
            "description": "RSI too close to 50",
        },
        {
            "name": "spread_filter",
            "fn": _check_spread,
            "description": "Excessive intraday spread",
        },
        {
            "name": "volume_filter",
            "fn": _check_volume,
            "description": "Below minimum trading volume",
        },
        {
            "name": "duplicate_filter",
            "fn": _check_duplicate,
            "description": f"Duplicate signal within {config.DUPLICATE_COOLDOWN_HOURS}h",
        },
    ]


# ── Public API ───────────────────────────────────────────────────────


def filter_signals(
    signals: list[dict[str, Any]],
    df: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter a list of signals through all active validators.

    Parameters
    ----------
    signals : list[dict]
        Raw signals from the engine (typically the bar-wise list).
    df : pd.DataFrame
        The OHLCV DataFrame used to generate the signals. Needed for
        spread/volume checks.

    Returns
    -------
    accepted : list[dict]
        Signals that passed all filters. Each dict gains a ``validated``
        key set to ``True``.
    rejected : list[dict]
        Signals that failed one or more filters. Each dict gains a
        ``rejection_reasons`` key (list of str) and ``validated`` set to
        ``False``.
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for sig in signals:
        # Tag the signal with its asset class for per-class filter lookups
        if "asset" not in sig:
            sig["asset"] = config.get_asset_class(sig.get("pair", ""))
        # Confidence score — same formula for every signal
        sig["confidence"] = calculate_confidence(sig)
        rejected_by: list[str] = []
        reasons: list[str] = []

        if sig["signal"] == "HOLD":
            # HOLD is always accepted — no filter needed
            sig["validated"] = True
            sig["rejected_by"] = []
            accepted.append(sig)
            continue

        for fdef in _all_filters():
            reason = fdef["fn"](sig, df)
            if reason is not None:
                rejected_by.append(fdef["name"])
                reasons.append(f"{fdef['name']}: {reason}")

        if reasons:
            sig["validated"] = False
            sig["rejected_by"] = rejected_by
            sig["rejection_reasons"] = reasons
            rejected.append(sig)
        else:
            sig["validated"] = True
            sig["rejected_by"] = []
            accepted.append(sig)

    return accepted, rejected


def describe_filters() -> list[dict[str, str]]:
    """Return a human-readable list of active filters (for CLI introspection)."""
    filters = [
        {"name": f["name"], "description": f["description"]}
        for f in _all_filters()
    ]
    # Add per-asset volume info
    classes = list(config.ASSET_FILTERS.keys())
    vol_descs = []
    for cls in classes:
        v = config.ASSET_FILTERS[cls].get("min_volume", 0)
        vol_descs.append(f"{cls}={v:,.0f}")
    filters.append({
        "name": "volume_by_asset",
        "description": f"Min volume per class: {'; '.join(vol_descs)}",
    })
    return filters
