"""Central tradability predicate — ONE source of truth for "can we trade this?".

WHY THIS EXISTS
===============
Suspension knowledge was scattered: ``config.SUSPENDED_SYMBOLS`` was honoured
by the auto-trader and by ``target_allocation.SUSPENDED``, but the *research*
surfaces (signal, ranking/opportunities, forecast, decision allocation lines,
newly-built benchmark universes) still treated a suspended counter as an
ordinary investable security. BAMB (suspended on the NSE since 2025-02-28)
kept emitting a score, a "Reduce" recommendation, a 24-month holding period
and a target weight — i.e. the platform advertised a security nobody can buy
or sell.

This module is the single predicate every consumer must call. It combines:

1. the STATIC list — ``config.SUSPENDED_SYMBOLS`` (e.g. BAMB); and
2. the DYNAMIC detector — ``trading.risk.illiquidity_detector``, which spots
   an OHLC-locked (O=H=L=C) run that is consistent with a suspension/halt
   even when the name was never pre-listed.

Contract
--------
* ``verdict(symbol, df=None)`` — full structured answer.
* ``is_tradable(symbol, df=None)`` — boolean.
* ``eligible_pairs()`` / ``filter_tradable()`` — universe helpers.

Suspended names stay VISIBLE as legacy/non-tradable holdings or monitoring
records (portfolio state, transactions, snapshots and the existing
``benchmark.json`` are never rewritten); they are only excluded from
*newly computed* investable output.

Pure and side-effect free: no network, no writes, no state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from . import config

REASON_TRADABLE = "tradable"
REASON_SUSPENDED = "suspended"
REASON_ILLIQUID = "illiquid"

STATUS_TRADABLE = "tradable"
STATUS_NON_TRADABLE = "non_tradable"

SUSPENDED_DETAIL = (
    "Suspended/halted on the NSE — NOT tradeable. It may still appear as a "
    "legacy holding or monitoring record, but no new signal, score, forecast, "
    "holding period, ranking, allocation target or benchmark exposure is "
    "produced for it."
)


def normalize_symbol(symbol: Any) -> str:
    """Uppercase/trim a symbol so ``bamb`` and ``BAMB`` agree."""
    return str(symbol or "").strip().upper()


def suspended_symbols() -> frozenset[str]:
    """The static suspended/halted universe (normalised).

    Read from ``config.SUSPENDED_SYMBOLS`` on every call so tests and
    operators can monkeypatch/reload config without a stale cache.
    """
    return frozenset(
        normalize_symbol(s) for s in getattr(config, "SUSPENDED_SYMBOLS", []) if s
    )


@dataclass(frozen=True)
class TradabilityVerdict:
    """The single structured tradability answer for one symbol."""

    symbol: str
    tradable: bool
    reason: str
    detail: str

    @property
    def status(self) -> str:
        return STATUS_TRADABLE if self.tradable else STATUS_NON_TRADABLE

    @property
    def suspended(self) -> bool:
        return self.reason == REASON_SUSPENDED

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "tradable": self.tradable,
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
        }


def _tradable(symbol: str) -> TradabilityVerdict:
    return TradabilityVerdict(symbol, True, REASON_TRADABLE,
                              "Eligible for the NSE equity universe.")


def static_verdict(symbol: Any) -> TradabilityVerdict:
    """Static-only verdict: is the symbol on the suspended/halted list?"""
    sym = normalize_symbol(symbol)
    if sym and sym in suspended_symbols():
        return TradabilityVerdict(sym, False, REASON_SUSPENDED, SUSPENDED_DETAIL)
    return _tradable(sym)


def is_suspended(symbol: Any) -> bool:
    """True if *symbol* is on the static suspended/halted list."""
    return not static_verdict(symbol).tradable


def _illiquidity_verdict(symbol: str, df: Any) -> Optional[TradabilityVerdict]:
    """Dynamic OHLC-lock check. Returns None when the name is not locked."""
    if df is None:
        return None
    try:
        if len(df) == 0:
            return None
    except TypeError:  # not a sized object
        return None
    try:
        from .risk.illiquidity_detector import detect_illiquidity
    except Exception:  # noqa: BLE001 — detector is best-effort, never fatal
        return None
    try:
        verdict = detect_illiquidity(symbol, df)
    except Exception:  # noqa: BLE001
        return None
    if getattr(verdict, "status", "healthy") == "locked":
        return TradabilityVerdict(
            symbol, False, REASON_ILLIQUID,
            f"{verdict.note} Non-tradable until the lock clears.",
        )
    return None


def verdict(symbol: Any, df: Any = None) -> TradabilityVerdict:
    """Full verdict: static suspension first, then the dynamic lock detector.

    ``df`` is optional — pass the symbol's OHLCV frame to enable the dynamic
    check; omit it (or pass ``None``) for a static-only answer.
    """
    static = static_verdict(symbol)
    if not static.tradable:
        return static
    if df is None:
        return static
    locked = _illiquidity_verdict(static.symbol, df)
    return locked or static


def is_tradable(symbol: Any, df: Any = None) -> bool:
    """The one predicate. True only if the symbol is tradable right now."""
    return verdict(symbol, df).tradable


def filter_tradable(
    symbols: Iterable[str],
    bars: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Return the tradable subset of *symbols*, preserving order.

    ``bars`` maps symbol → OHLCV frame; when supplied the dynamic lock
    detector runs too.
    """
    out: list[str] = []
    for sym in symbols:
        df = bars.get(sym) if bars else None
        if is_tradable(sym, df):
            out.append(sym)
    return out


def non_tradable(
    symbols: Iterable[str],
    bars: Optional[Mapping[str, Any]] = None,
) -> list[TradabilityVerdict]:
    """Return verdicts for the NON-tradable members of *symbols* (for reporting)."""
    out: list[TradabilityVerdict] = []
    for sym in symbols:
        df = bars.get(sym) if bars else None
        verdict_ = verdict(sym, df)
        if not verdict_.tradable:
            out.append(verdict_)
    return out


def eligible_pairs(
    pairs: Optional[Iterable[str]] = None,
    bars: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """The tradable universe — ``config.PAIRS`` minus suspended/locked names."""
    if pairs is None:
        pairs = list(getattr(config, "PAIRS", []))
    return filter_tradable(pairs, bars)
