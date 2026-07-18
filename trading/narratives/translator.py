"""Translate technical values to plain English sentences.

Each public function returns a dict with at least ``what`` and
``why_it_matters`` keys. Templates compose these pieces into
paragraphs without needing to know the underlying thresholds.

Output contract (shared by all translators)
-------------------------------------------
::

    {
        "what": "...",            # What happened (factual)
        "why_it_matters": "...",  # Why it matters (significance)
    }

Every sentence is written at a Grade 8 reading level. There is no
mention of indicator names (RSI / SMA / ATR) unless the caller
explicitly opts in via a verbose mode elsewhere — that policy is
enforced by the templates, not here.
"""
from __future__ import annotations

from typing import Any, Optional


# ── RSI ──────────────────────────────────────────────────────────────


def translate_rsi(rsi: Optional[float]) -> dict[str, str]:
    """Translate an RSI(14) value into plain English.

    Buckets follow the spec exactly: ``[0, 20) [20, 35) [35, 45)
    [45, 55) [55, 65) [65, 80) [80, 100+]``.

    ``None`` (data unavailable) returns a neutral, non-fabricated
    payload.
    """
    if rsi is None:
        return {
            "what": "Momentum indicators are not yet available — not enough data.",
            "why_it_matters": (
                "We prefer to stay silent than invent a reading, so this "
                "asset is being reported without a momentum read."
            ),
        }

    rsi = float(rsi)
    if rsi < 20:
        return {
            "what": "Price has fallen sharply and is deeply oversold.",
            "why_it_matters": (
                "Markets this stretched often see a bounce, but the trend "
                "remains firmly downward until buyers step in."
            ),
        }
    if rsi < 35:
        return {
            "what": "Selling pressure is dominant but weakening.",
            "why_it_matters": (
                "The asset is in oversold territory — early signs of "
                "stabilization, but confirmation is still required."
            ),
        }
    if rsi < 45:
        return {
            "what": "The market is weak but showing early signs of recovery.",
            "why_it_matters": (
                "Momentum is beginning to improve, but buyers have not yet "
                "demonstrated sustained strength."
            ),
        }
    if rsi < 55:
        return {
            "what": "The market is balanced with no clear direction.",
            "why_it_matters": (
                "Neither buyers nor sellers have seized control. This is a "
                "waiting zone."
            ),
        }
    if rsi < 65:
        return {
            "what": "Buyers are beginning to gain an edge.",
            "why_it_matters": (
                "Momentum is shifting in favor of the bulls, but the move "
                "needs to be sustained to confirm."
            ),
        }
    if rsi < 80:
        return {
            "what": "Buying pressure is strong and the uptrend is intact.",
            "why_it_matters": (
                "The market is in overbought territory — strong, but "
                "approaching a zone where reversals become more likely."
            ),
        }
    # rsi >= 80
    return {
        "what": "The market is extremely overbought.",
        "why_it_matters": (
            "The rally has been powerful, but stretched conditions increase "
            "the risk of a pullback."
        ),
    }


# ── Trend (SMA alignment) ───────────────────────────────────────────


def translate_trend(
    price: Optional[float],
    sma20: Optional[float],
    sma50: Optional[float],
) -> dict[str, str]:
    """Translate the price/SMA20/SMA50 alignment into a trend narrative.

    Returns a neutral payload if any input is missing — we never want
    to claim a trend we can't actually compute.
    """
    if price is None or sma20 is None or sma50 is None:
        return {
            "what": "Trend direction is not yet clear — not enough history.",
            "why_it_matters": (
                "We need at least a few months of price history before we "
                "can speak confidently about the trend."
            ),
        }

    price = float(price)
    sma20 = float(sma20)
    sma50 = float(sma50)

    if price > sma20 and sma20 > sma50:
        return {
            "what": "The asset is in a strong uptrend with short-term momentum "
                    "accelerating ahead of the longer-term trend.",
            "why_it_matters": (
                "Both timeframes agree, which is a healthy sign for anyone "
                "looking to add to a position."
            ),
        }
    if price > sma20 and sma20 < sma50:
        return {
            "what": "Short-term momentum is positive, but the longer-term "
                    "trend remains downward.",
            "why_it_matters": (
                "This is a potential reversal pattern — worth watching, but "
                "not yet confirmed by the longer-term average."
            ),
        }
    if price < sma20 and sma20 < sma50:
        return {
            "what": "The asset is in a clear downtrend with selling pressure "
                    "across all timeframes.",
            "why_it_matters": (
                "Both the short and long-term averages are pointing lower — "
                "this is the kind of price action where capital preservation "
                "matters more than catching a falling knife."
            ),
        }
    # price < sma20 and sma20 > sma50
    return {
        "what": "The long-term trend is still up, but recent price action "
                "has weakened.",
        "why_it_matters": (
            "This is a warning signal — the bigger picture is intact but "
            "near-term pressure is building. Watch for either a re-acceleration "
            "or a deeper break."
        ),
    }


# ── Volatility ───────────────────────────────────────────────────────


def translate_volatility(atr_percentile: Optional[float]) -> dict[str, str]:
    """Translate a volatility reading into plain English.

    The input is a 0-100 percentile (0 = quietest regime the asset
    has ever seen, 100 = loudest). The spec splits at 30 and 70.
    """
    if atr_percentile is None:
        return {
            "what": "Volatility data is not yet available.",
            "why_it_matters": (
                "We need more history to estimate how unusual the current "
                "swings are."
            ),
        }

    p = float(atr_percentile)
    if p < 30:
        return {
            "what": "Price movements are calm and stable.",
            "why_it_matters": (
                "Low volatility environments favour position-building — "
                "smaller daily swings mean entries don't require perfect timing."
            ),
        }
    if p <= 70:
        return {
            "what": "Volatility is within normal ranges.",
            "why_it_matters": (
                "The market is behaving typically — no special caution needed "
                "from a volatility perspective."
            ),
        }
    return {
        "what": "Price swings are wider than usual.",
        "why_it_matters": (
            "Higher volatility increases both opportunity and risk — "
            "expect larger gains and larger drawdowns until conditions settle."
        ),
    }


# ── Score (0-100 → recommendation + plain language) ────────────────


def translate_score(score: Optional[float]) -> dict[str, str]:
    """Translate a 0-100 aggregate score into recommendation + language.

    The recommendation label is taken from ``config.TIER_*`` so the
    five tiers stay consistent across the system.
    """
    from .. import config  # local import to avoid cycles in callers

    if score is None:
        return {
            "recommendation": config.TIER_HOLD,
            "what": "We do not yet have a confident read on this asset.",
            "why_it_matters": (
                "No clear edge either way — patience is the right play until "
                "more data arrives."
            ),
        }

    score = float(score)

    # Walk the spec's buckets in order so the labels stay in sync with config.
    if score >= 90:
        return {
            "recommendation": config.TIER_STRONG_ACCUMULATE,
            "what": "This is one of the strongest opportunities across all "
                    "monitored markets.",
            "why_it_matters": (
                "The evidence strongly supports building a position for the "
                "long term."
            ),
        }
    if score >= 75:
        return {
            "recommendation": config.TIER_ACCUMULATE,
            "what": "This asset shows favourable characteristics for "
                    "long-term investment.",
            "why_it_matters": (
                "Consider building a position."
            ),
        }
    if score >= 50:
        return {
            "recommendation": config.TIER_HOLD,
            "what": "The outlook is neutral — no urgent action is required.",
            "why_it_matters": (
                "Hold existing positions and monitor."
            ),
        }
    if score >= 25:
        return {
            "recommendation": config.TIER_REDUCE,
            "what": "Risk factors are beginning to outweigh the positives.",
            "why_it_matters": (
                "Consider reducing exposure."
            ),
        }
    return {
        "recommendation": config.TIER_AVOID,
        "what": "The evidence strongly suggests staying away.",
        "why_it_matters": (
            "Multiple risk factors are present with limited upside potential."
        ),
    }


# ── Signal action (BUY / SELL / HOLD) ───────────────────────────────


def translate_signal(signal_type: Optional[str]) -> dict[str, str]:
    """Translate the latest bar's BUY/SELL/HOLD verdict into a sentence."""
    s = (signal_type or "HOLD").upper()

    if s == "BUY":
        return {
            "what": "A fresh bullish crossover has just been registered.",
            "why_it_matters": (
                "Short-term momentum has turned positive against the "
                "longer-term trend — historically a constructive entry point."
            ),
        }
    if s == "SELL":
        return {
            "what": "A bearish crossover has just been registered.",
            "why_it_matters": (
                "Short-term momentum has turned negative against the "
                "longer-term trend — a defensive posture is warranted."
            ),
        }
    if s == "HOLD":
        return {
            "what": "There is no fresh crossover — the market is still "
                    "in its previous posture.",
            "why_it_matters": (
                "No new signal means existing positions can be held without "
                "imminent reason to act."
            ),
        }
    # WATCH or unknown — neutral
    return {
        "what": "The latest bar is being watched but produced no actionable signal.",
        "why_it_matters": (
            "Conditions are interesting but not yet conclusive — sit tight."
        ),
    }


# ── Convenience aggregator ───────────────────────────────────────────


def summarise_factors(
    factors: dict[str, float],
) -> dict[str, Any]:
    """Summarise an 8-factor ranking block as a short plain-language list.

    Returns a dict ``{"strong": [...], "weak": [...], "neutral": [...]}``
    where each entry is a ``(factor_label, score)`` tuple. The lists
    are sorted by score (strong descending, weak ascending) so the
    caller can pick the top two from each.

    The labels are mapped through :data:`FACTOR_PLAIN_LABELS` so the
    caller can use the result directly in beginner-facing copy
    (e.g. "the trend is supportive", "recent momentum is strong") —
    internal lens names like ``relative_strength`` never appear in
    the output.
    """
    if not factors:
        return {"strong": [], "weak": [], "neutral": []}

    items = [(FACTOR_PLAIN_LABELS.get(k, k.replace("_", " ").title()),
              float(v)) for k, v in factors.items()]
    items.sort(key=lambda kv: kv[1], reverse=True)

    strong = [it for it in items if it[1] >= 67]
    weak = [it for it in items if it[1] <= 33]
    neutral = [it for it in items if 33 < it[1] < 67]

    return {
        "strong": strong,
        "weak": weak,
        "neutral": neutral,
    }


# Plain-language labels for the 8 ranking factors. These are the
# phrasings the user actually reads in the Daily Investment Brief —
# they describe *what the lens measures* in beginner vocabulary, not
# the internal scoring name. Add new factors here when you add a new
# lens; never let underscored names leak into the brief.
FACTOR_PLAIN_LABELS: dict[str, str] = {
    "trend":             "trend direction",
    "momentum":          "recent momentum",
    "volatility":        "price stability",
    "liquidity":         "trading activity",
    "relative_strength": "performance vs market",
    "risk":              "risk profile",
    "regime":            "market regime",
    "alignment":         "indicator agreement",
}
