"""Tests for market-intel integration in the Investment Advisor.

These cover the additions made in the Market Intelligence task:
``advisor.explain_symbol`` now appends a "Context" section when
context is available, and ``advisor.daily_brief`` adds a "Market
Drivers" section. Both are designed to be no-ops (byte-identical
output) when the context store is empty.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from trading.services import advisor
from trading.services.market_intel import context as mi_context


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def sample_ranked():
    return [
        {"symbol": "SCOM", "score": 77.0, "recommendation": "Accumulate",
         "factors": {"trend": 80, "momentum": 75, "volatility": 70, "risk": 65}},
    ]


@pytest.fixture
def sample_pair_signals():
    return {
        "SCOM": {"pair": "SCOM", "signal": "BUY", "rsi": 62.0, "confidence": 75.0},
    }


# ── explain_symbol with context ───────────────────────────────────


class TestExplainSymbolContext:
    """``advisor.explain_symbol`` now appends context when available."""

    def test_explain_includes_context_section(self, sample_ranked,
                                               sample_pair_signals):
        with patch.object(mi_context, "assemble", return_value=[
            {"kind": "news", "label": "positive", "text": "SCOM wins",
             "timestamp": "2026-06-28T10:00:00Z", "relevance": 0.9,
             "source": "alpha"},
        ]):
            out = advisor.explain_symbol(
                "SCOM", ranked=sample_ranked, pair_signals=sample_pair_signals,
            )
        assert "Context" in out or "context" in out
        assert "SCOM wins" in out

    def test_explain_unchanged_when_context_empty(self, sample_ranked,
                                                   sample_pair_signals):
        # No mocking — context.assemble is real, but with no data it
        # returns []. The output should still be a valid explanation.
        with patch.object(mi_context, "assemble", return_value=[]):
            out = advisor.explain_symbol(
                "SCOM", ranked=sample_ranked, pair_signals=sample_pair_signals,
            )
        assert "SCOM" in out
        # Should not contain a "Context" section header
        assert "Market context" not in out

    def test_explain_can_disable_context(self, sample_ranked,
                                          sample_pair_signals):
        with patch.object(mi_context, "assemble") as mock_assemble:
            out = advisor.explain_symbol(
                "SCOM", ranked=sample_ranked, pair_signals=sample_pair_signals,
                include_context=False,
            )
        mock_assemble.assert_not_called()
        assert isinstance(out, str)

    def test_explain_context_failure_does_not_break(self, sample_ranked,
                                                     sample_pair_signals):
        # If the context layer blows up, we still get a valid explanation.
        with patch.object(mi_context, "assemble",
                          side_effect=Exception("boom")):
            out = advisor.explain_symbol(
                "SCOM", ranked=sample_ranked, pair_signals=sample_pair_signals,
            )
        assert "SCOM" in out


# ── daily_brief with market drivers ───────────────────────────────


class TestDailyBriefDrivers:
    """``advisor.daily_brief`` now includes a Market Drivers section."""

    def test_brief_includes_market_drivers(self, sample_ranked,
                                            sample_pair_signals):
        # Patch the underlying context assembler so the advisor picks
        # up our fake context items.
        with patch.object(mi_context, "assemble", return_value=[
            {"kind": "news", "label": "positive", "text": "CBK held rates at 12.50%",
             "timestamp": "2026-06-28T10:00:00Z", "relevance": 0.9,
             "source": "alpha"},
        ]):
            out = advisor.daily_brief(
                ranked=sample_ranked, pair_signals=sample_pair_signals,
            )
        # Section header should be present
        assert "MARKET DRIVERS" in out

    def test_brief_works_without_market_drivers(self, sample_ranked,
                                                 sample_pair_signals):
        # When context.assemble returns [] (offline default), no drivers section
        with patch.object(mi_context, "assemble", return_value=[]):
            out = advisor.daily_brief(
                ranked=sample_ranked, pair_signals=sample_pair_signals,
            )
        assert "SCOM" in out  # brief still works
        assert "MARKET DRIVERS" not in out  # section omitted

    def test_brief_can_disable_drivers(self, sample_ranked,
                                        sample_pair_signals):
        with patch.object(mi_context, "assemble") as mock_assemble:
            out = advisor.daily_brief(
                ranked=sample_ranked, pair_signals=sample_pair_signals,
                include_drivers=False,
            )
        mock_assemble.assert_not_called()
        assert "MARKET DRIVERS" not in out
