"""Per-asset signal explanation template.

Used by ``trading explain SYMBOL`` and ``trading signal SYMBOL`` to
produce a paragraph that a beginner can read end-to-end. The
output is *plain language only* — no raw RSI / SMA / ATR values
unless the caller explicitly asks for verbose mode.
"""
from __future__ import annotations

from typing import Optional

from ..narratives import confidence as confidence_mod, risk as risk_mod, translator


def format_signal_explanation(
    symbol: str,
    score: float,
    recommendation: str,
    rsi: Optional[float] = None,
    trend: Optional[str] = None,
    confidence: Optional[float] = None,
    confidence_score: Optional[float] = None,
) -> str:
    """Build a single plain-English paragraph for one symbol.

    The four questions the spec mandates are answered in order:

    1. What happened?       — opening line based on recommendation
    2. Why does it matter?  — momentum (RSI) + trend read
    3. Should I act?        — the recommendation sentence
    4. What are the risks?  — risk sentences
    """
    score = _safe_float(score) or 0.0
    rsi_v = _safe_float(rsi)
    # Accept either kwarg name — call sites differ across CLI commands
    conf_v = _safe_float(confidence) if confidence is not None else _safe_float(confidence_score)

    # 1 + 3. Recommendation framing — start with the symbol so the
    # reader can identify the asset in a long thread of output.
    score_payload = translator.translate_score(score)
    lead = f"{symbol}: {score_payload['what']}"
    act = score_payload["why_it_matters"]

    # 2. Momentum / trend read
    momentum = translator.translate_rsi(rsi_v)
    trend_payload = translator.translate_trend(
        price=None, sma20=None, sma50=None,
    ) if not trend else _trend_from_label(trend)

    # 4. Risk read
    risk_lines = risk_mod.describe_risks(
        {"symbol": symbol, "score": score,
         "recommendation": recommendation, "factors": {}},
        rsi=rsi_v, trend=trend,
    )

    # 5. Confidence (if we have a number)
    conf_line = ""
    if conf_v is not None:
        conf_line = " " + confidence_mod.explain_confidence(conf_v)

    parts = [
        lead,
        momentum["what"] + " " + momentum["why_it_matters"],
        trend_payload["what"] + " " + trend_payload["why_it_matters"],
        act,
    ]
    if conf_line:
        parts.append(conf_line.strip())
    parts.append("Risks: " + " ".join(risk_lines))

    return " ".join(parts)


def _trend_from_label(trend: str) -> dict[str, str]:
    """Coerce a string label ('up'/'down'/'flat') into the translator shape."""
    t = (trend or "").lower()
    if t == "up":
        return {
            "what": "Short-term direction is up.",
            "why_it_matters": "Recent price action is positive.",
        }
    if t == "down":
        return {
            "what": "Short-term direction is down.",
            "why_it_matters": "Recent price action is negative.",
        }
    return {
        "what": "Short-term direction is flat.",
        "why_it_matters": "The market is moving sideways without a clear lean.",
    }


def _safe_float(x):
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
