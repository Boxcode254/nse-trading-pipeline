"""Daily Investment Brief template.

Composes the full Telegram-friendly briefing from the building
blocks. Hard-capped at 4096 characters to fit a single Telegram
message with headroom for the framework's bookkeeping.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from . import opportunities as tpl_opportunities
from . import portfolio as tpl_portfolio
from . import warnings as tpl_warnings


TELEGRAM_MAX_CHARS = 4096


def format_brief(
    date: str,
    market_mood: str,
    opportunity_score: float,
    top_opportunities: Iterable[dict[str, Any]],
    assets_to_avoid: Iterable[dict[str, Any]],
    market_summary: str,
    portfolio_suggestions: str,
    top_n_opportunities: int = 3,
    top_n_warnings: int = 3,
    ranked: Optional[Iterable[dict[str, Any]]] = None,
    drivers_block: str = "",
    opportunities_analyzed: Optional[int] = None,
    strongest_symbol: Optional[str] = None,
    strongest_confidence: Optional[float] = None,
    _shrink_depth: int = 0,
) -> str:
    """Assemble the full Daily Investment Brief.

    Returns a string under :data:`TELEGRAM_MAX_CHARS` characters.
    If the natural composition overflows, we trim the warnings
    section first, then the opportunities, then drop the portfolio
    line. The ``_shrink_depth`` counter prevents infinite recursion
    when an upstream caller passed an oversized portfolio block
    (we only re-enter the overflow cascade once).
    """
    top_opportunities = list(top_opportunities) if top_opportunities else []
    assets_to_avoid = list(assets_to_avoid) if assets_to_avoid else []
    ranked = list(ranked) if ranked else top_opportunities

    sections: list[str] = []

    # ── Header ──────────────────────────────────────────────────────
    sections.append(f"🌅  DAILY INVESTMENT BRIEF — {date}")

    # Add learning integration header if data is available
    if opportunities_analyzed is not None and strongest_symbol is not None:
        confidence_str = f" (confidence {strongest_confidence:.0f}%)" if strongest_confidence is not None else ""
        sections.append(f"Today I analyzed {opportunities_analyzed} opportunities. Strongest opportunity is {strongest_symbol}{confidence_str}.")
    else:
        sections.append(f"Mood: {market_mood}  ·  Opportunity: {opportunity_score:.0f}/100")

    sections.append("")

    # ── Market context (single line — the brief avoids duplicating
    # the structured summary section). The structured summary lives
    # in its own template and is used when called directly.
    sections.append(market_summary)
    sections.append("")

    # ── Market drivers (optional — only if the upstream context
    # layer produced something useful). Added before opportunities
    # so the reader sees "why" before "what".
    if drivers_block:
        sections.append(drivers_block)
        sections.append("")

    # ── Top opportunities ──────────────────────────────────────────
    sections.append(
        tpl_opportunities.format_opportunities(
            top_opportunities, top_n=top_n_opportunities
        )
    )
    sections.append("")

    # ── Assets to avoid ────────────────────────────────────────────
    sections.append(
        tpl_warnings.format_warnings(
            assets_to_avoid, top_n=top_n_warnings
        )
    )
    sections.append("")

    # ── Portfolio suggestion ───────────────────────────────────────
    if portfolio_suggestions:
        sections.append(portfolio_suggestions)
    elif ranked:
        sections.append(tpl_portfolio.format_portfolio(ranked))

    body = "\n".join(sections)

    if len(body) <= TELEGRAM_MAX_CHARS:
        return body

    # If we've already attempted one overflow shrink, the next
    # attempt would just re-enter with the same args — abort the
    # recursion and let the hard-truncate fallback take over.
    if _shrink_depth >= 1:
        return body[: TELEGRAM_MAX_CHARS - 3] + "..."

    # Overflow cascade: try smaller warnings, then smaller
    # opportunities, then drop the portfolio. The original
    # top_opportunities / assets_to_avoid lists are kept (not the
    # already-rendered strings) so we can re-render with a smaller N.
    body = _shrink_warnings(sections, top_opportunities, assets_to_avoid,
                            max(top_n_warnings - 1, 1),
                            market_summary, portfolio_suggestions,
                            date, market_mood, opportunity_score,
                            top_n_opportunities, ranked,
                            drivers_block,
                            opportunities_analyzed, strongest_symbol, strongest_confidence,
                            _shrink_depth + 1)
    if len(body) <= TELEGRAM_MAX_CHARS:
        return body
    body = _shrink_opportunities(sections, top_opportunities, assets_to_avoid,
                                 max(top_n_opportunities - 1, 1),
                                 market_summary, portfolio_suggestions,
                                 date, market_mood, opportunity_score,
                                 top_n_warnings, ranked,
                                 drivers_block,
                                 opportunities_analyzed, strongest_symbol, strongest_confidence,
                                 _shrink_depth + 1)
    if len(body) <= TELEGRAM_MAX_CHARS:
        return body
    body = _drop_portfolio(sections, top_opportunities, assets_to_avoid,
                           date, market_mood, opportunity_score,
                           market_summary, top_n_opportunities,
                           top_n_warnings, ranked,
                           drivers_block,
                           opportunities_analyzed, strongest_symbol, strongest_confidence,
                           _shrink_depth + 1)
    if len(body) <= TELEGRAM_MAX_CHARS:
        return body

    # Final fallback: hard truncate with an ellipsis
    return body[: TELEGRAM_MAX_CHARS - 3] + "..."


def _shrink_warnings(
    sections: list[str],
    top_opportunities: list[dict],
    assets_to_avoid: list[dict],
    new_n: int,
    market_summary: str,
    portfolio_suggestions: str,
    date: str,
    market_mood: str,
    opportunity_score: float,
    top_n_opportunities: int,
    ranked: list[dict] | None,
    drivers_block: str,
    opportunities_analyzed: Optional[int],
    strongest_symbol: Optional[str],
    strongest_confidence: Optional[float],
    _shrink_depth: int,
) -> str:
    """Re-render the full brief with a smaller warnings count."""
    return format_brief(
        date=date,
        market_mood=market_mood,
        opportunity_score=opportunity_score,
        top_opportunities=top_opportunities,
        assets_to_avoid=assets_to_avoid[:new_n] if new_n else [],
        market_summary=market_summary,
        portfolio_suggestions=portfolio_suggestions,
        top_n_opportunities=top_n_opportunities,
        top_n_warnings=new_n,
        ranked=ranked,
        drivers_block=drivers_block,
        opportunities_analyzed=opportunities_analyzed,
        strongest_symbol=strongest_symbol,
        strongest_confidence=strongest_confidence,
        _shrink_depth=_shrink_depth,
    )


def _shrink_opportunities(
    sections: list[str],
    top_opportunities: list[dict],
    assets_to_avoid: list[dict],
    new_n: int,
    market_summary: str,
    portfolio_suggestions: str,
    date: str,
    market_mood: str,
    opportunity_score: float,
    top_n_warnings: int,
    ranked: list[dict] | None,
    drivers_block: str,
    opportunities_analyzed: Optional[int],
    strongest_symbol: Optional[str],
    strongest_confidence: Optional[float],
    _shrink_depth: int,
) -> str:
    """Re-render the full brief with a smaller opportunities count."""
    return format_brief(
        date=date,
        market_mood=market_mood,
        opportunity_score=opportunity_score,
        top_opportunities=top_opportunities[:new_n] if new_n else [],
        assets_to_avoid=assets_to_avoid,
        market_summary=market_summary,
        portfolio_suggestions=portfolio_suggestions,
        top_n_opportunities=new_n,
        top_n_warnings=top_n_warnings,
        ranked=ranked,
        drivers_block=drivers_block,
        opportunities_analyzed=opportunities_analyzed,
        strongest_symbol=strongest_symbol,
        strongest_confidence=strongest_confidence,
        _shrink_depth=_shrink_depth,
    )


def _drop_portfolio(
    sections: list[str],
    top_opportunities: list[dict],
    assets_to_avoid: list[dict],
    date: str,
    market_mood: str,
    opportunity_score: float,
    market_summary: str,
    top_n_opportunities: int,
    top_n_warnings: int,
    ranked: list[dict] | None,
    drivers_block: str,
    opportunities_analyzed: Optional[int],
    strongest_symbol: Optional[str],
    strongest_confidence: Optional[float],
    _shrink_depth: int,
) -> str:
    """Re-render the full brief without the portfolio section."""
    return format_brief(
        date=date,
        market_mood=market_mood,
        opportunity_score=opportunity_score,
        top_opportunities=top_opportunities,
        assets_to_avoid=assets_to_avoid,
        market_summary=market_summary,
        portfolio_suggestions="",  # drop entirely
        top_n_opportunities=top_n_opportunities,
        top_n_warnings=top_n_warnings,
        ranked=ranked,
        drivers_block=drivers_block,
        opportunities_analyzed=opportunities_analyzed,
        strongest_symbol=strongest_symbol,
        strongest_confidence=strongest_confidence,
        _shrink_depth=_shrink_depth,
    )