"""Generate plain-language risk descriptions for an asset.

Combines the ranking entry's factors, the latest signal, and the
caller-supplied indicator values (RSI, etc.) into a list of
risk-focused sentences. The list is intentionally 1-4 items so
templates can drop it into a paragraph or a bullet list.
"""
from __future__ import annotations

from typing import Any, Optional


def describe_risks(
    entry: dict[str, Any],
    rsi: Optional[float] = None,
    trend: Optional[str] = None,
) -> list[str]:
    """Return a list of plain-English risk sentences for one asset.

    Parameters
    ----------
    entry : dict
        A ranking entry as produced by ``ranking.ranker`` —
        needs at least ``recommendation`` and ``factors``.
    rsi : float, optional
        Latest RSI(14) reading for this asset. Used to detect
        stretched conditions (overbought / oversold) that aren't
        visible in the aggregate score.
    trend : str, optional
        Latest trend label (``"up"`` / ``"down"`` / ``"flat"``).
        Defaults to the entry's signal if present.

    Notes
    -----
    The function is defensive — it never raises on missing data; it
    just emits fewer sentences.
    """
    if not entry or not isinstance(entry, dict):
        return ["Insufficient information to assess risk."]

    recommendation = (entry.get("recommendation") or "").strip()
    factors = entry.get("factors") or {}
    rsi_v = _safe_float(rsi)

    risks: list[str] = []

    # ── Tier-specific framing ─────────────────────────────────────
    if recommendation == "Strong Accumulate":
        risks.append(
            "Even the strongest opportunities carry risk — a sharp market "
            "rotation could erode the thesis quickly."
        )
    elif recommendation == "Accumulate":
        risks.append(
            "The position is supported by current evidence, but valuations "
            "and macro conditions can shift without warning."
        )
    elif recommendation == "Hold":
        risks.append(
            "Holding carries opportunity cost — capital tied up here can't be "
            "deployed to a stronger setup elsewhere."
        )
    elif recommendation == "Reduce":
        risks.append(
            "The risk is that further weakness compounds — trimming now limits "
            "the drawdown if conditions deteriorate."
        )
    elif recommendation == "Avoid":
        risks.append(
            "The risk is primarily to the downside — the evidence points to "
            "limited upside and meaningful drawdown potential."
        )

    # ── RSI-driven risks ──────────────────────────────────────────
    if rsi_v is not None:
        if rsi_v >= 80:
            risks.append(
                "The main risk is that the uptrend may be exhausted — "
                "buying pressure is extremely stretched and a pullback is "
                "more likely than a continuation."
            )
        elif rsi_v >= 65:
            risks.append(
                "Momentum is strong but approaching a zone where reversals "
                "become more common — set clear exit rules in advance."
            )
        elif rsi_v <= 20:
            risks.append(
                "Falling knives can keep falling — a deeply oversold reading "
                "doesn't guarantee a bottom."
            )
        elif 45 <= rsi_v <= 55:
            risks.append(
                "The primary risk is range-bound uncertainty — without clear "
                "direction, the market is prone to whipsaw."
            )

    # ── Factor-driven risks ───────────────────────────────────────
    trend_f = _safe_float(factors.get("trend"))
    momentum_f = _safe_float(factors.get("momentum"))
    risk_f = _safe_float(factors.get("risk"))
    alignment_f = _safe_float(factors.get("alignment"))

    if trend_f is not None and trend_f < 25:
        risks.append(
            "The trend picture is poor — the asset is below both its short "
            "and long-term averages with no reversal signal yet."
        )

    if momentum_f is not None and momentum_f < 25 and risk_f is not None and risk_f < 25:
        risks.append(
            "Momentum and risk metrics are both weak — this is a "
            "defensive asset at best, not a buying opportunity."
        )

    if alignment_f is not None and alignment_f < 30:
        risks.append(
            "The internal indicators disagree on direction, which increases "
            "the chance of contradictory signals in the days ahead."
        )

    # If we still have no risks at all, emit a generic defensive note
    if not risks:
        risks.append(
            "All monitored factors are within normal ranges — keep position "
            "size conservative and review on any decisive price move."
        )

    return risks


def _safe_float(x: Any) -> Optional[float]:
    """Return a float or None — protects against NaN, strings, and None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    import math
    if math.isnan(v):
        return None
    return v
