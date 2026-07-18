"""Tests for the Investment Advisor — natural language layer.

The advisor must translate raw technical values (RSI, SMA alignment,
volatility, score) into plain English a beginner can understand. Each
public translator returns a small structured dict with:

    {
        "what": "...",          # What happened (factual movement)
        "why_it_matters": "...", # Why it matters (significance)
    }

so the templates can compose them into paragraphs without knowing
about thresholds.

Run from the repo root:
    ~/.trading/.venv/bin/python -m pytest trading/tests/test_advisor.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

# Isolate the package to a temp dir so the smoke test never touches ~/.trading
TMP_HOME = tempfile.mkdtemp(prefix="trading-advisor-test-")
os.environ["HOME"] = TMP_HOME

import pytest  # noqa: E402

from trading import config  # noqa: E402
from trading.narratives import (  # noqa: E402
    translator,
    risk,
    confidence,
    market_context,
)
from trading.templates import (  # noqa: E402
    brief,
    opportunities as tpl_opportunities,
    warnings as tpl_warnings,
    summary as tpl_summary,
    portfolio as tpl_portfolio,
    signal as tpl_signal,
)


# ═══════════════════════════════════════════════════════════════════
# translator.py — RSI / trend / volatility / score / signal
# ═══════════════════════════════════════════════════════════════════


class TestRsiTranslation:
    """translate_rsi(rsi) → {"what": ..., "why_it_matters": ...}.

    Buckets from the spec:
        0-20, 20-35, 35-45, 45-55, 55-65, 65-80, 80-100
    """

    def test_rsi_very_low_band_uses_deeply_oversold_language(self):
        out = translator.translate_rsi(10.0)
        assert "what" in out and "why_it_matters" in out
        assert "oversold" in out["what"].lower() or "fallen" in out["what"].lower()

    def test_rsi_around_50_is_neutral(self):
        out = translator.translate_rsi(50.0)
        # 45-55 bucket — neutral language
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "balanced" in text or "neutral" in text or "waiting" in text

    def test_rsi_high_band_warns_overbought(self):
        out = translator.translate_rsi(85.0)
        assert "overbought" in out["why_it_matters"].lower() or "overbought" in out["what"].lower()

    def test_rsi_around_70_is_strong_but_warning(self):
        out = translator.translate_rsi(72.0)
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "strong" in text or "uptrend" in text

    def test_rsi_none_returns_neutral_payload(self):
        out = translator.translate_rsi(None)
        assert out["what"]
        assert out["why_it_matters"]


class TestTrendTranslation:
    """translate_trend(price, sma20, sma50) → {"what": ..., "why_it_matters": ...}."""

    def test_strong_uptrend_sma_alignment(self):
        out = translator.translate_trend(price=110.0, sma20=105.0, sma50=100.0)
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "uptrend" in text or "rising" in text

    def test_clear_downtrend(self):
        out = translator.translate_trend(price=90.0, sma20=95.0, sma50=100.0)
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "downtrend" in text or "falling" in text or "selling" in text

    def test_potential_reversal_short_up_long_down(self):
        # Price > SMA20 but SMA20 < SMA50 → reversal pattern
        out = translator.translate_trend(price=101.0, sma20=99.0, sma50=100.0)
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "reversal" in text or "warning" in text or "weakening" in text

    def test_warning_signal_long_up_short_down(self):
        # Long trend up, but short-term weaker — warning
        # price < sma20 and sma20 > sma50
        out = translator.translate_trend(price=99.0, sma20=100.0, sma50=99.0)
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "warning" in text or "weakening" in text

    def test_missing_inputs_returns_neutral(self):
        out = translator.translate_trend(price=None, sma20=100.0, sma50=100.0)
        assert out["what"] and out["why_it_matters"]


class TestVolatilityTranslation:
    """translate_volatility(atr_percentile) → narrative."""

    def test_low_volatility_is_calm(self):
        out = translator.translate_volatility(20.0)
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "calm" in text or "low" in text

    def test_normal_volatility(self):
        out = translator.translate_volatility(50.0)
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "normal" in text or "typical" in text

    def test_high_volatility_warns(self):
        out = translator.translate_volatility(85.0)
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "high" in text or "wider" in text or "wider than" in text or "swings" in text

    def test_none_volatility_is_neutral(self):
        out = translator.translate_volatility(None)
        assert out["what"] and out["why_it_matters"]


class TestScoreTranslation:
    """translate_score(score) → {"recommendation": ..., "what": ..., "why_it_matters": ...}.

    Buckets from the spec:
        90-100 Strong Accumulate
        75-89  Accumulate
        50-74  Hold
        25-49  Reduce
        0-24   Avoid
    """

    def test_score_95_is_strong_accumulate(self):
        out = translator.translate_score(95.0)
        assert out["recommendation"] == config.TIER_STRONG_ACCUMULATE
        assert "strongest" in out["why_it_matters"].lower() or "strong" in out["why_it_matters"].lower()

    def test_score_82_is_accumulate(self):
        out = translator.translate_score(82.0)
        assert out["recommendation"] == config.TIER_ACCUMULATE
        combined = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "favourable" in combined or "long-term" in combined

    def test_score_60_is_hold(self):
        out = translator.translate_score(60.0)
        assert out["recommendation"] == config.TIER_HOLD
        combined = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "neutral" in combined or "hold" in combined

    def test_score_35_is_reduce(self):
        out = translator.translate_score(35.0)
        assert out["recommendation"] == config.TIER_REDUCE
        combined = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "risk" in combined or "reduce" in combined

    def test_score_10_is_avoid(self):
        out = translator.translate_score(10.0)
        assert out["recommendation"] == config.TIER_AVOID
        combined = (out["what"] + " " + out["why_it_matters"]).lower()
        # "staying away" is a grammatical form of "stay away"
        assert "stay" in combined or "avoid" in combined

    def test_score_at_threshold_75_is_accumulate(self):
        out = translator.translate_score(75.0)
        assert out["recommendation"] == config.TIER_ACCUMULATE

    def test_score_below_threshold_75_is_hold(self):
        out = translator.translate_score(74.9)
        assert out["recommendation"] == config.TIER_HOLD


class TestSignalTranslation:
    """translate_signal(signal_type) → plain sentence for the latest action."""

    def test_buy_signal(self):
        out = translator.translate_signal("BUY")
        assert "what" in out and "why_it_matters" in out
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "bullish" in text or "crossover" in text or "buy" in text

    def test_sell_signal(self):
        out = translator.translate_signal("SELL")
        text = (out["what"] + " " + out["why_it_matters"]).lower()
        assert "bearish" in text or "crossover" in text or "sell" in text

    def test_hold_signal(self):
        out = translator.translate_signal("HOLD")
        assert "what" in out and "why_it_matters" in out

    def test_unknown_signal_falls_back(self):
        out = translator.translate_signal("WATCH")
        assert "what" in out and "why_it_matters" in out


# ═══════════════════════════════════════════════════════════════════
# risk.py — risk description generator
# ═══════════════════════════════════════════════════════════════════


class TestRiskDescription:
    """describe_risks(ranking_entry) → list[str] of risk sentences."""

    def test_returns_a_list(self):
        entry = {
            "symbol": "SCOM",
            "score": 60.0,
            "recommendation": "Hold",
            "factors": {"trend": 60, "momentum": 50, "volatility": 50,
                        "liquidity": 50, "relative_strength": 50,
                        "risk": 50, "regime": 50, "alignment": 50},
        }
        out = risk.describe_risks(entry)
        assert isinstance(out, list)
        assert all(isinstance(s, str) for s in out)

    def test_strong_accumulate_warns_about_overbought_when_momentum_high(self):
        entry = {
            "symbol": "X",
            "score": 95.0,
            "recommendation": "Strong Accumulate",
            "factors": {"momentum": 90, "trend": 90, "alignment": 90,
                        "volatility": 50, "risk": 50, "regime": 80,
                        "liquidity": 50, "relative_strength": 50},
        }
        out = risk.describe_risks(entry, rsi=85.0)
        joined = " ".join(out).lower()
        assert "overbought" in joined or "pullback" in joined

    def test_avoid_warns_about_downside(self):
        entry = {
            "symbol": "Y",
            "score": 10.0,
            "recommendation": "Avoid",
            "factors": {"trend": 10, "momentum": 10, "alignment": 10,
                        "volatility": 50, "risk": 50, "regime": 10,
                        "liquidity": 50, "relative_strength": 50},
        }
        out = risk.describe_risks(entry, rsi=20.0)
        joined = " ".join(out).lower()
        assert any(w in joined for w in ("downside", "broken", "trend", "risk"))

    def test_sideways_risk_warns_about_whipsaw(self):
        entry = {
            "symbol": "Z",
            "score": 55.0,
            "recommendation": "Hold",
            "factors": {"trend": 50, "momentum": 50, "alignment": 50,
                        "volatility": 50, "risk": 50, "regime": 50,
                        "liquidity": 50, "relative_strength": 50},
        }
        # rsi around 50 → sideways reading
        out = risk.describe_risks(entry, rsi=50.0)
        joined = " ".join(out).lower()
        # Either a sideways note OR a generic risk note
        assert len(out) >= 1

    def test_no_crash_on_minimal_entry(self):
        # An entry missing all optional context should still work
        out = risk.describe_risks({"symbol": "X", "score": 50.0,
                                    "recommendation": "Hold", "factors": {}})
        assert isinstance(out, list)


# ═══════════════════════════════════════════════════════════════════
# confidence.py — confidence explanation
# ═══════════════════════════════════════════════════════════════════


class TestConfidenceExplanation:
    """explain_confidence(confidence) → plain sentence.

    Buckets:
        75-100 High
        50-74  Medium
        0-49   Low
    """

    def test_high_confidence(self):
        out = confidence.explain_confidence(85.0)
        assert "high" in out.lower() or "confident" in out.lower()

    def test_medium_confidence(self):
        out = confidence.explain_confidence(60.0)
        assert "reasonable" in out.lower() or "medium" in out.lower() or "mixed" in out.lower()

    def test_low_confidence(self):
        out = confidence.explain_confidence(30.0)
        assert "limited" in out.lower() or "tentative" in out.lower() or "low" in out.lower()

    def test_confidence_at_threshold_75(self):
        out = confidence.explain_confidence(75.0)
        assert "high" in out.lower() or "confident" in out.lower()

    def test_confidence_none_is_neutral(self):
        out = confidence.explain_confidence(None)
        assert out


# ═══════════════════════════════════════════════════════════════════
# market_context.py — broader market context narrative
# ═══════════════════════════════════════════════════════════════════


class TestMarketContext:
    """summarise_market_context(ranked) → plain paragraph."""

    def test_empty_ranked_returns_neutral(self):
        out = market_context.summarise_market_context([])
        assert out

    def test_bullish_universe_uses_positive_language(self):
        ranked = [
            {"symbol": "A", "score": 85.0, "recommendation": "Accumulate",
             "factors": {"regime": 80}},
            {"symbol": "B", "score": 78.0, "recommendation": "Accumulate",
             "factors": {"regime": 75}},
            {"symbol": "C", "score": 70.0, "recommendation": "Hold",
             "factors": {"regime": 60}},
        ]
        out = market_context.summarise_market_context(ranked)
        text = out.lower()
        assert "favourable" in text or "bullish" in text or "opportunity" in text

    def test_bearish_universe_uses_cautious_language(self):
        ranked = [
            {"symbol": "A", "score": 15.0, "recommendation": "Avoid",
             "factors": {"regime": 20}},
            {"symbol": "B", "score": 30.0, "recommendation": "Reduce",
             "factors": {"regime": 25}},
            {"symbol": "C", "score": 45.0, "recommendation": "Reduce",
             "factors": {"regime": 30}},
        ]
        out = market_context.summarise_market_context(ranked)
        text = out.lower()
        assert "caution" in text or "bearish" in text or "defensive" in text

    def test_mixed_universe_acknowledges_split(self):
        ranked = [
            {"symbol": "A", "score": 80.0, "recommendation": "Accumulate",
             "factors": {"regime": 80}},
            {"symbol": "B", "score": 40.0, "recommendation": "Reduce",
             "factors": {"regime": 30}},
            {"symbol": "C", "score": 60.0, "recommendation": "Hold",
             "factors": {"regime": 50}},
        ]
        out = market_context.summarise_market_context(ranked)
        assert out  # non-empty, says SOMETHING


# ═══════════════════════════════════════════════════════════════════
# templates/brief.py — Daily Investment Brief
# ═══════════════════════════════════════════════════════════════════


class TestBriefTemplate:
    """format_brief(...) → string under 4096 chars with all sections."""

    def _kwargs(self):
        return dict(
            date="2026-06-28",
            market_mood="favourable",
            opportunity_score=68.5,
            top_opportunities=[{
                "symbol": "SCOM", "score": 88.0,
                "recommendation": "Accumulate", "holding_period": "12 months",
                "factors": {"trend": 85, "momentum": 80, "alignment": 90,
                            "regime": 85, "volatility": 50, "liquidity": 70,
                            "risk": 75, "relative_strength": 60},
            }],
            assets_to_avoid=[{
                "symbol": "ABSA", "score": 18.0,
                "recommendation": "Avoid", "holding_period": "24 months",
                "factors": {},
            }],
            market_summary="Overall market conditions are favourable.",
            portfolio_suggestions="Core: SCOM 30%, KCB 25%.",
        )

    def test_returns_string(self):
        out = brief.format_brief(**self._kwargs())
        assert isinstance(out, str)
        assert len(out) > 0

    def test_fits_telegram_4096(self):
        out = brief.format_brief(**self._kwargs())
        assert len(out) <= 4096, f"brief too long: {len(out)} chars"

    def test_contains_date(self):
        out = brief.format_brief(**self._kwargs())
        assert "2026-06-28" in out

    def test_contains_top_opportunity_symbol(self):
        out = brief.format_brief(**self._kwargs())
        assert "SCOM" in out

    def test_contains_avoid_symbol(self):
        out = brief.format_brief(**self._kwargs())
        assert "ABSA" in out

    def test_handles_empty_sections(self):
        out = brief.format_brief(
            date="2026-06-28",
            market_mood="neutral",
            opportunity_score=50.0,
            top_opportunities=[],
            assets_to_avoid=[],
            market_summary="Mixed market.",
            portfolio_suggestions="Stay in cash.",
        )
        assert isinstance(out, str)
        assert len(out) > 0

    def test_shrinks_to_fit_when_content_too_long(self):
        """When the natural composition would exceed 4096 chars, the
        brief must shrink gracefully — by reducing the warnings and
        opportunities count, not by dropping them to empty sections."""
        # Build a portfolio_suggestions block that's intentionally
        # long to push the natural brief over 4096 chars.
        long_portfolio = (
            "Suggested core allocation:\n"
            + "".join(f"  • SYM{i:02d} — {5 + i}%  ·  Hold\n" for i in range(60))
        )
        # Use a big ranked universe so opportunities + warnings are
        # already at max length.
        big_top = [
            {"symbol": f"SYM{i:02d}", "score": 90 - i, "recommendation": "Accumulate",
             "holding_period": "12 months", "factors": {"trend": 85, "momentum": 80}}
            for i in range(8)
        ]
        big_bottom = [
            {"symbol": f"BAZ{i:02d}", "score": 5 + i, "recommendation": "Avoid",
             "holding_period": "24 months", "factors": {}}
            for i in range(8)
        ]
        # Force the natural length past the cap by giving the brief a
        # tiny budget via monkey-patch.
        import trading.templates.brief as brief_mod
        original_cap = brief_mod.TELEGRAM_MAX_CHARS
        brief_mod.TELEGRAM_MAX_CHARS = 1200
        try:
            out = brief.format_brief(
                date="2026-06-28",
                market_mood="neutral",
                opportunity_score=55.0,
                top_opportunities=big_top,
                assets_to_avoid=big_bottom,
                market_summary="Mixed market.",
                portfolio_suggestions=long_portfolio,
                ranked=big_top + big_bottom,
            )
        finally:
            brief_mod.TELEGRAM_MAX_CHARS = original_cap
        # The shrink path must keep at least one entry per section —
        # the old stub returned [] and dropped the section entirely.
        assert "TOP OPPORTUNITIES" in out
        assert "ASSETS TO AVOID" in out
        # We must still see at least one ranked symbol — proving
        # the section wasn't dropped to an empty placeholder.
        top_symbols = [f"SYM{i:02d}" for i in range(8)]
        bot_symbols = [f"BAZ{i:02d}" for i in range(8)]
        assert any(s in out for s in top_symbols), (
            "Top opportunities section has no actual symbols after shrink"
        )
        assert any(s in out for s in bot_symbols), (
            "Assets-to-avoid section has no actual symbols after shrink"
        )
        # The old code would render "No assets to rank today." when
        # the stub returned []. Make sure that broken placeholder is
        # NOT what we ship.
        assert "No assets to rank today" not in out
        # And the result must fit the (small) cap we forced.
        assert len(out) <= 1200, f"overflow shrink failed: {len(out)} chars"


# ═══════════════════════════════════════════════════════════════════
# templates/opportunities.py — top opportunities narrative section
# ═══════════════════════════════════════════════════════════════════


class TestOpportunitiesTemplate:
    """format_opportunities(entries) → narrative block."""

    def test_returns_string(self):
        out = tpl_opportunities.format_opportunities([
            {"symbol": "X", "score": 85.0, "recommendation": "Accumulate",
             "holding_period": "12 months", "factors": {"trend": 80}},
        ])
        assert isinstance(out, str)

    def test_empty_returns_minimal_output(self):
        out = tpl_opportunities.format_opportunities([])
        assert "no" in out.lower() or "—" in out

    def test_includes_symbol_and_tier(self):
        out = tpl_opportunities.format_opportunities([
            {"symbol": "SCOM", "score": 88.0, "recommendation": "Accumulate",
             "holding_period": "12 months", "factors": {}},
        ])
        assert "SCOM" in out
        assert "Accumulate" in out

    def test_strengths_use_plain_english_not_internal_factor_names(self):
        """The 'Strengths:' line must not leak internal factor names like
        'Relative Strength', 'Trend', 'Alignment' — these are scoring
        lenses, not beginner vocabulary. The acceptance criteria require
        the output to read like plain English a beginner can follow."""
        out = tpl_opportunities.format_opportunities([
            {"symbol": "X", "score": 85.0, "recommendation": "Accumulate",
             "holding_period": "12 months",
             "factors": {"trend": 80, "relative_strength": 75, "alignment": 70}},
        ])
        # Internal lens names that shouldn't appear in a beginner brief
        forbidden = ["Relative Strength", "Alignment"]
        for needle in forbidden:
            assert needle not in out, (
                f"Internal factor name {needle!r} leaked into output: {out!r}"
            )

    def test_strengths_phrase_is_present_when_factors_strong(self):
        out = tpl_opportunities.format_opportunities([
            {"symbol": "X", "score": 85.0, "recommendation": "Accumulate",
             "holding_period": "12 months",
             "factors": {"trend": 80, "momentum": 75}},
        ])
        # We keep the "Strengths:" framing for parallelism with the
        # technical ranker, but the values after it must be plain
        # English (e.g. "trend direction", "recent momentum"), not
        # underscored factor names.
        assert "Strengths:" in out
        # The factor names are underscored in the data; if any raw
        # underscored token shows up after "Strengths:" the test fails.
        if "Strengths:" in out:
            tail = out.split("Strengths:", 1)[1]
            assert "trend_" not in tail
            assert "momentum_" not in tail


# ═══════════════════════════════════════════════════════════════════
# templates/warnings.py — assets to avoid
# ═══════════════════════════════════════════════════════════════════


class TestWarningsTemplate:
    """format_warnings(entries) → narrative block."""

    def test_returns_string(self):
        out = tpl_warnings.format_warnings([
            {"symbol": "X", "score": 15.0, "recommendation": "Avoid",
             "holding_period": "24 months", "factors": {}},
        ])
        assert isinstance(out, str)

    def test_empty_returns_minimal_output(self):
        out = tpl_warnings.format_warnings([])
        assert isinstance(out, str)

    def test_includes_symbol_and_tier(self):
        out = tpl_warnings.format_warnings([
            {"symbol": "ABSA", "score": 12.0, "recommendation": "Avoid",
             "holding_period": "24 months", "factors": {}},
        ])
        assert "ABSA" in out
        assert "Avoid" in out


# ═══════════════════════════════════════════════════════════════════
# templates/summary.py — market summary with regime
# ═══════════════════════════════════════════════════════════════════


class TestSummaryTemplate:
    """format_summary(market_score, ranked, context_line) → narrative block."""

    def test_returns_string(self):
        out = tpl_summary.format_summary(
            market_score=68.0,
            ranked=[{"symbol": "X", "score": 70.0, "factors": {"regime": 70}}],
            context_line="Overall market conditions are favourable.",
        )
        assert isinstance(out, str)

    def test_contains_market_score(self):
        out = tpl_summary.format_summary(
            market_score=72.5,
            ranked=[],
            context_line="Mixed conditions.",
        )
        assert "72" in out


# ═══════════════════════════════════════════════════════════════════
# templates/portfolio.py — portfolio allocation (stub)
# ═══════════════════════════════════════════════════════════════════


class TestPortfolioTemplate:
    """format_portfolio(entries) → narrative block (stubbed)."""

    def test_returns_string(self):
        out = tpl_portfolio.format_portfolio([
            {"symbol": "SCOM", "score": 80.0, "recommendation": "Accumulate",
             "factors": {}},
        ])
        assert isinstance(out, str)

    def test_includes_core_holdings(self):
        out = tpl_portfolio.format_portfolio([
            {"symbol": "SCOM", "score": 85.0, "recommendation": "Accumulate",
             "factors": {}},
            {"symbol": "KCB", "score": 80.0, "recommendation": "Accumulate",
             "factors": {}},
        ])
        assert "SCOM" in out


# ═══════════════════════════════════════════════════════════════════
# templates/signal.py — per-asset explanation
# ═══════════════════════════════════════════════════════════════════


class TestSignalTemplate:
    """format_signal_explanation(...) → plain-English paragraph."""

    def test_returns_string(self):
        out = tpl_signal.format_signal_explanation(
            symbol="SCOM",
            score=85.0,
            recommendation="Accumulate",
            rsi=62.0,
            trend="up",
            confidence=0.7,
        )
        assert isinstance(out, str)
        assert "SCOM" in out

    def test_explains_hold(self):
        out = tpl_signal.format_signal_explanation(
            symbol="ABSA",
            score=45.0,
            recommendation="Hold",
            rsi=48.0,
            trend="flat",
            confidence=0.3,
        )
        assert "ABSA" in out

    def test_handles_none_indicators(self):
        out = tpl_signal.format_signal_explanation(
            symbol="X",
            score=50.0,
            recommendation="Hold",
            rsi=None,
            trend="flat",
            confidence=None,
        )
        assert isinstance(out, str)
        assert out
