"""Keyword-based sentiment scorer.

Returns a float in ``[-1.0, +1.0]`` for a single headline. This is the
ground truth for every news item, and the simplest possible approach:
weighted keyword matches, no LLM, no model downloads.

The lexicon is hand-curated for the NSE / Kenyan market context (rate
hikes, CBK decisions, dividends, M-PESA, etc.). Numbers came from
intuition + a quick survey of common financial-news verbs. It's not
perfect, but it's transparent and explainable — exactly the property
the spec asks for.

Public surface
--------------

- ``score(text)``        — float, -1.0 to +1.0
- ``classify(score)``     — "positive" | "negative" | "neutral"
- ``flags(text)``         — list of key-term strings present in the text
"""
from __future__ import annotations

from typing import Iterable

# Curated lexicon. Weights are -1.0 (max bearish) to +1.0 (max bullish).
# We deliberately err on the side of obvious words; the test suite
# covers the high-signal cases.
_BULLISH: dict[str, float] = {
    # Financial outcomes
    "profit": 0.18, "surge": 0.20, "beat": 0.15, "growth": 0.15,
    "record": 0.12, "wins": 0.12, "win": 0.10, "upgrade": 0.15,
    "boost": 0.12, "rise": 0.08, "rises": 0.08, "rising": 0.08,
    "rally": 0.18, "expansion": 0.15, "expand": 0.12, "gains": 0.12,
    "gain": 0.08, "strong": 0.10, "outperform": 0.18, "exceed": 0.12,
    "exceeds": 0.12, "above": 0.04, "improved": 0.10, "improve": 0.08,
    "dividend": 0.15, "dividends": 0.15, "buyback": 0.18,
    "acquisition": 0.10, "acquire": 0.10, "acquires": 0.10,
    "partnership": 0.10, "launch": 0.10, "launches": 0.10,
    "breakthrough": 0.18, "milestone": 0.15, "approval": 0.12,
    "approved": 0.12, "recover": 0.10, "recovered": 0.10,
    "recovering": 0.08, "stable": 0.04, "stability": 0.06,
    "supportive": 0.10, "eased": 0.06, "ease": 0.04,
    "investment": 0.10, "invest": 0.08, "invests": 0.08,
    "opportunity": 0.10, "opportunities": 0.10,
    "leading": 0.08, "leader": 0.08, "innovation": 0.10,
    "robust": 0.10, "healthy": 0.08,
}

_BEARISH: dict[str, float] = {
    "loss": 0.18, "losses": 0.20, "slump": 0.20, "plunge": 0.22,
    "plunges": 0.22, "fall": 0.10, "falls": 0.10, "falling": 0.10,
    "decline": 0.12, "declines": 0.12, "declining": 0.12,
    "drop": 0.12, "drops": 0.12, "downgrade": 0.18,
    "scandal": 0.25, "fraud": 0.25, "probe": 0.15,
    "investigation": 0.15, "lawsuit": 0.15, "sue": 0.15, "sued": 0.15,
    "miss": 0.12, "missed": 0.15, "weak": 0.15, "weakness": 0.15,
    "warning": 0.15, "warns": 0.15, "warned": 0.15,
    "cut": 0.10, "cuts": 0.10, "slashing": 0.18, "slash": 0.15,
    "layoff": 0.15, "layoffs": 0.18, "shutdown": 0.18,
    "bankruptcy": 0.30, "insolvent": 0.25, "default": 0.22,
    "debt": 0.08, "concerns": 0.12, "concern": 0.10,
    "risk": 0.08, "risks": 0.08, "risky": 0.10,
    "volatility": 0.06, "turbulent": 0.15, "turbulence": 0.15,
    "crash": 0.25, "crisis": 0.20, "recession": 0.20,
    "inflation": 0.06, "hike": 0.10, "hikes": 0.10,
    "rate hike": 0.12, "rate cut": 0.04,  # rate cut is mildly bearish
    "fine": 0.15, "fined": 0.15, "penalty": 0.15, "penalties": 0.15,
    "underperform": 0.15, "below": 0.04, "misses": 0.10,
    "suspend": 0.15, "suspended": 0.15, "halt": 0.15, "halted": 0.15,
    "negative": 0.10, "downturn": 0.15,
    # A small set of common negative context nouns
    "losses mount": 0.18,
}

# Known key terms — surfaced via ``flags(text)``. These overlap with the
# scoring lexicon but are kept separate so the advisor can render them
# explicitly ("rate hike", "dividend", "acquisition", etc.).
_FLAGS: tuple[str, ...] = (
    "rate hike", "rate cut", "dividend", "acquisition", "merger",
    "scandal", "fraud", "probe", "earnings", "guidance", "downgrade",
    "upgrade", "buyback", "layoff", "lawsuit", "ipo", "split",
    "5g", "4g", "m-pesa", "expansion", "launch", "approval", "recession",
    "inflation", "cpi", "rate decision", "fed", "cbk",
    "banking", "telecom", "consumer", "energy", "manufacturing",
)


def score(text: str | None) -> float:
    """Return a sentiment score in [-1.0, +1.0] for ``text``.

    The score is the mean of the bullish/bearish keyword weights that
    appear in the text, bounded to [-1, +1]. Empty / None input → 0.0.
    """
    if not text:
        return 0.0
    lowered = text.lower()
    bull: list[float] = []
    bear: list[float] = []
    for word, w in _BULLISH.items():
        if word in lowered:
            bull.append(w)
    for word, w in _BEARISH.items():
        if word in lowered:
            bear.append(w)
    if not bull and not bear:
        return 0.0
    raw = sum(bull) - sum(bear)
    # Normalise: divide by the total weight observed so a single
    # keyword in a long headline isn't overstated. Cap at ±1.
    denom = max(sum(bull) + sum(bear), 1.0)
    s = raw / denom
    if s > 1.0:
        return 1.0
    if s < -1.0:
        return -1.0
    return round(s, 3)


def classify(s: float) -> str:
    """Map a score to a human label."""
    if s > 0.2:
        return "positive"
    if s < -0.2:
        return "negative"
    return "neutral"


def flags(text: str) -> list[str]:
    """Return the list of known key terms that appear in ``text``."""
    if not text:
        return []
    lowered = text.lower()
    return [term for term in _FLAGS if term in lowered]
