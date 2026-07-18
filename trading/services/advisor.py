"""Investment Advisor — orchestrator for natural-language reports.

This is the single point of integration between the technical
engines (signals, ranking) and the narrative layer. The CLI calls
into here; the CLI itself contains *no* narrative logic.

Public surface
--------------
explain_symbol(symbol, ...)     — per-asset paragraph
daily_brief(date, ...)          — full Telegram brief
enrich_opportunities(ranked)    — top opportunities block
enrich_warnings(ranked)         — worst-scoring assets block
summarise_market(ranked)        — market summary block
portfolio(ranked)               — portfolio allocation block
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .. import config
from ..narratives import market_context
from ..services.market_intel import context as mi_context
from ..templates import (
    brief as brief_tpl,
    opportunities as opp_tpl,
    portfolio as port_tpl,
    signal as signal_tpl,
    summary as summary_tpl,
    warnings as warn_tpl,
)


# ── Market opportunity score (avg of all asset scores) ────────────


def _market_opportunity_score(ranked: Iterable[dict]) -> float:
    items = list(ranked) if ranked else []
    if not items:
        return 0.0
    total = 0.0
    n = 0
    for r in items:
        s = r.get("score")
        if s is None:
            continue
        try:
            v = float(s)
        except (TypeError, ValueError):
            continue
        if v != v:  # NaN
            continue
        total += v
        n += 1
    return round(total / n, 1) if n else 0.0


def _mood_from_score(score: float) -> str:
    if score >= 70:
        return "bullish"
    if score <= 35:
        return "bearish"
    return "neutral"


# ── Public functions ───────────────────────────────────────────────


def explain_symbol(
    symbol: str,
    ranked: Optional[Iterable[dict]] = None,
    pair_signals: Optional[dict[str, dict]] = None,
    verbose: bool = False,
    include_context: bool = True,
) -> str:
    """Return a plain-English explanation for one symbol.

    Parameters
    ----------
    symbol : str
        The asset to explain (e.g. ``"SCOM"``).
    ranked : iterable, optional
        Pre-computed ranking entries. If omitted, the function looks
        up the symbol in ``pair_signals`` only.
    pair_signals : dict, optional
        Per-pair signal dicts (from ``signals.engine.generate_signals``).
        Used to extract the latest RSI / confidence / signal type.
    verbose : bool, default ``False``
        When True, append a structured "Raw values:" footer that
        exposes the underlying indicator numbers (RSI, score). The
        beginner-friendly paragraph is unchanged — the verbose data
        is additive, not a replacement.
    include_context : bool, default ``True``
        When True (default), append a "Market context" section built
        from the market-intelligence layer (news, macro calendar,
        earnings). If the context layer returns no items, no section
        is added — the output is byte-identical to the pre-context
        version. If the context layer raises, we silently skip.

    Notes
    -----
    Falls back to a polite "we don't have data" message if the
    symbol is unknown. Never raises.
    """
    ranked = list(ranked) if ranked else []
    pair_signals = pair_signals or {}

    # Find the ranking entry for this symbol
    entry = next((e for e in ranked if e.get("symbol") == symbol), None)
    sig = pair_signals.get(symbol) or {}

    if entry is None and not sig:
        return (
            f"{symbol}: we don't have any data for this asset right now. "
            "Check that the ticker is correct and try again later."
        )

    score = float(entry.get("score", 0.0)) if entry else 0.0
    recommendation = (entry.get("recommendation", config.TIER_HOLD)
                      if entry else config.TIER_HOLD)
    rsi = _safe_float(sig.get("rsi"))
    conf = _safe_float(sig.get("confidence"))
    sig_type = sig.get("signal", "HOLD")

    explanation = signal_tpl.format_signal_explanation(
        symbol=symbol,
        score=score,
        recommendation=recommendation,
        rsi=rsi,
        trend=None,  # we don't have a price trend label in the signal dict
        confidence=conf,
    )

    # ── Market context (best-effort) ────────────────────────────
    if include_context:
        context_block = _safe_context_block(symbol)
        if context_block:
            explanation = f"{explanation}\n\n{context_block}"

    if verbose:
        raw_bits = []
        if rsi is not None:
            raw_bits.append(f"RSI={rsi:.1f}")
        raw_bits.append(f"score={score:.1f}/100")
        if conf is not None:
            # Confidence is on a 0-100 scale (see signals/validator.py —
            # calculate_confidence returns 0..100). Display as a number
            # out of 100, not as a percentage (which would multiply by
            # 100 and look like 1040% for a value of 10.4).
            raw_bits.append(f"confidence={conf:.0f}/100")
        if entry is not None:
            factors = entry.get("factors") or {}
            for k in ("trend", "momentum", "volatility", "risk"):
                v = _safe_float(factors.get(k))
                if v is not None:
                    raw_bits.append(f"{k}={v:.0f}")
        explanation = (
            f"{explanation}\n\nRaw values: " + ", ".join(raw_bits)
        )

    return explanation


def _safe_context_block(symbol: str) -> str:
    """Best-effort: build a market-context block for ``symbol``.

    Returns "" if the context layer has no data or raises. The
    caller decides what to do with the empty string.
    """
    try:
        items = mi_context.assemble(symbol, max_items=3)
    except Exception:  # noqa: BLE001
        return ""
    if not items:
        return ""
    return mi_context.format_block(symbol, items)


def daily_brief(
    date: Optional[str] = None,
    ranked: Optional[Iterable[dict]] = None,
    pair_signals: Optional[dict[str, dict]] = None,
    include_drivers: bool = True,
    opportunities_analyzed: Optional[int] = None,
    strongest_symbol: Optional[str] = None,
    strongest_confidence: Optional[float] = None,
) -> str:
    """Assemble the full Daily Investment Brief.

    The output is hard-capped at Telegram's 4096 char limit.

    Parameters
    ----------
    include_drivers : bool, default ``True``
        When True, append a "Market Drivers" section to the brief
        with the top macro context items for the watched universe.
        When False (or when drivers are empty), the brief is
        identical to the pre-market-intel version.
    opportunities_analyzed : int, optional
        Total number of opportunities analyzed in the scan.
    strongest_symbol : str, optional
        Symbol of the strongest opportunity (highest ranked).
    strongest_confidence : float, optional
        Confidence score (0-100) of the strongest opportunity.
    """
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ranked = list(ranked) if ranked else []
    pair_signals = pair_signals or {}

    score = _market_opportunity_score(ranked)
    mood = _mood_from_score(score)

    # Top 3 by score
    sorted_ranked = sorted(
        ranked,
        key=lambda e: _safe_float(e.get("score")) or 0.0,
        reverse=True,
    )
    top = sorted_ranked[:3]
    # Bottom 3 (worst)
    bottom = sorted_ranked[-3:][::-1]  # worst first

    # Build the context line
    context_line = market_context.summarise_market_context(ranked)

    # Build the per-section strings
    summary_block = summary_tpl.format_summary(
        market_score=score, ranked=ranked, context_line=context_line,
    )
    opps_block = opp_tpl.format_opportunities(top, top_n=3)
    warns_block = warn_tpl.format_warnings(bottom, top_n=3)
    portfolio_block = port_tpl.format_portfolio(ranked)

    # Build the optional market-drivers block
    drivers_block = ""
    if include_drivers:
        drivers_block = _market_drivers(ranked)

    return brief_tpl.format_brief(
        date=date,
        market_mood=mood,
        opportunity_score=score,
        top_opportunities=top,
        assets_to_avoid=bottom,
        market_summary=summary_block,
        portfolio_suggestions=portfolio_block,
        top_n_opportunities=3,
        top_n_warnings=3,
        ranked=ranked,
        drivers_block=drivers_block,
        opportunities_analyzed=opportunities_analyzed,
        strongest_symbol=strongest_symbol,
        strongest_confidence=strongest_confidence,
    )


def _market_drivers(ranked: Iterable[dict], *, max_items: int = 4) -> str:
    """Return a multi-line "Market Drivers" string for the brief.

    Picks the highest-relevance context items across the watched
    universe and formats them as a bullet list. Returns "" if
    nothing relevant is available — the caller should omit the
    section in that case.
    """
    ranked = list(ranked) if ranked else []
    # Aggregate context across the top-ranked symbols. We score
    # by relevance and dedupe.
    all_items: list[dict] = []
    seen: list[str] = []
    for entry in ranked:
        sym = entry.get("symbol")
        if not sym:
            continue
        try:
            items = mi_context.assemble(sym, max_items=2)
        except Exception:  # noqa: BLE001
            continue
        for it in items:
            text = (it.get("text") or "").lower().strip()
            if not text or text in seen:
                continue
            seen.append(text)
            all_items.append(it)

    if not all_items:
        return ""

    all_items.sort(
        key=lambda i: (i.get("relevance", 0.0), i.get("timestamp", "")),
        reverse=True,
    )
    all_items = all_items[:max_items]

    lines = ["🌍  MARKET DRIVERS"]
    for it in all_items:
        kind = it.get("kind", "")
        label = it.get("label", "")
        text = it.get("text", "")
        prefix = f"[{label}]" if label else ""
        kind_tag = f"({kind})" if kind else ""
        lines.append(f"  • {prefix} {text} {kind_tag}".strip())
    return "\n".join(lines)


def enrich_opportunities(
    ranked: Iterable[dict],
    top_n: int = 3,
) -> str:
    """Return a narrative block for the top-N opportunities."""
    ranked = list(ranked) if ranked else []
    sorted_ranked = sorted(
        ranked,
        key=lambda e: _safe_float(e.get("score")) or 0.0,
        reverse=True,
    )
    return opp_tpl.format_opportunities(sorted_ranked, top_n=top_n)


def enrich_warnings(
    ranked: Iterable[dict],
    top_n: int = 3,
) -> str:
    """Return a narrative block for the worst-N assets."""
    ranked = list(ranked) if ranked else []
    sorted_ranked = sorted(
        ranked,
        key=lambda e: _safe_float(e.get("score")) or 0.0,
    )
    return warn_tpl.format_warnings(sorted_ranked, top_n=top_n)


def summarise_market(ranked: Iterable[dict]) -> str:
    """Return a short market summary (score + context)."""
    ranked = list(ranked) if ranked else []
    score = _market_opportunity_score(ranked)
    context_line = market_context.summarise_market_context(ranked)
    return summary_tpl.format_summary(
        market_score=score, ranked=ranked, context_line=context_line,
    )


def portfolio(ranked: Iterable[dict]) -> str:
    """Return the portfolio allocation block."""
    ranked = list(ranked) if ranked else []
    return port_tpl.format_portfolio(ranked)


# ── Helpers ────────────────────────────────────────────────────────


def _safe_float(x: Any) -> Optional[float]:
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
