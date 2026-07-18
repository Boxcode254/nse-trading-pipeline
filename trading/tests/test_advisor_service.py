"""Tests for the Investment Advisor orchestrator.

The advisor glues the narrative generators and templates together.
It is the only place that knows about the full data shape (ranking
+ signal). Tests cover:

- explain_symbol(symbol)            — full per-asset explanation
- daily_brief(ranked, pair_signals) — full Telegram brief
- enrich_opportunities(ranked)      — ranked list with narrative
- enrich_warnings(ranked)           — worst-scoring with narrative
- summarise_market(ranked)          — short market summary
- portfolio(ranked)                 — portfolio allocation block

Run:
    cd ~/.trading && .venv/bin/python -m pytest trading/tests/test_advisor_service.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

TMP_HOME = tempfile.mkdtemp(prefix="trading-advisor-svc-test-")
os.environ["HOME"] = TMP_HOME

import pytest  # noqa: E402

from trading import config  # noqa: E402
from trading.services import advisor  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────


def _entry(symbol: str, score: float, tier: str,
           factors: dict | None = None) -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "recommendation": tier,
        "factors": factors or {"trend": score, "momentum": score,
                               "volatility": 50, "liquidity": 50,
                               "relative_strength": 50, "risk": 50,
                               "regime": 50, "alignment": score},
        "holding_period": "12 months",
        "rank": 1,
        "reason": f"Test entry for {symbol}",
    }


def _signal(symbol: str, rsi: float = 55.0, conf: float = 0.6,
            signal_type: str = "BUY") -> dict:
    return {
        "pair": symbol,
        "signal": signal_type,
        "rsi": rsi,
        "confidence": conf,
        "price": 100.0,
        "sma_fast": 99.0,
        "sma_slow": 98.0,
    }


@pytest.fixture
def sample_ranked():
    return [
        _entry("SCOM", 88.0, "Accumulate"),
        _entry("KCB", 76.0, "Accumulate"),
        _entry("EQTY", 60.0, "Hold"),
        _entry("EABL", 35.0, "Reduce"),
        _entry("ABSA", 15.0, "Avoid"),
    ]


@pytest.fixture
def sample_pair_signals():
    return {
        "SCOM": _signal("SCOM", rsi=62.0, conf=0.7, signal_type="HOLD"),
        "KCB": _signal("KCB", rsi=55.0, conf=0.55, signal_type="HOLD"),
        "EQTY": _signal("EQTY", rsi=50.0, conf=0.5, signal_type="HOLD"),
    }


# ── Tests ──────────────────────────────────────────────────────────


class TestExplainSymbol:
    """advisor.explain_symbol(symbol) → plain-English string for CLI."""

    def test_returns_string_for_known_symbol(self, sample_ranked, sample_pair_signals):
        out = advisor.explain_symbol("SCOM", ranked=sample_ranked,
                                     pair_signals=sample_pair_signals)
        assert isinstance(out, str)
        assert "SCOM" in out

    def test_returns_fallback_for_unknown_symbol(self, sample_ranked, sample_pair_signals):
        out = advisor.explain_symbol("UNKNOWN", ranked=sample_ranked,
                                     pair_signals=sample_pair_signals)
        # Still a string; may explain why we can't help
        assert isinstance(out, str)

    def test_uses_pair_signal_rsi(self, sample_ranked, sample_pair_signals):
        out = advisor.explain_symbol("SCOM", ranked=sample_ranked,
                                     pair_signals=sample_pair_signals)
        # The explanation should incorporate the momentum read (RSI 62)
        text = out.lower()
        # RSI 62 → "buyers are beginning to gain an edge" or similar
        assert "buyer" in text or "momentum" in text or "edge" in text

    def test_risks_are_present(self, sample_ranked, sample_pair_signals):
        out = advisor.explain_symbol("SCOM", ranked=sample_ranked,
                                     pair_signals=sample_pair_signals)
        # Must include a "Risks" section per the spec's 4-question contract
        assert "risk" in out.lower()

    def test_default_output_has_no_raw_indicator_values(self,
                                                       sample_ranked,
                                                       sample_pair_signals):
        """The spec mandates beginner-friendly output: no raw RSI / SMA / ATR
        numbers leak into the default explanation. Power-users can opt in
        via ``verbose=True``."""
        out = advisor.explain_symbol("SCOM", ranked=sample_ranked,
                                     pair_signals=sample_pair_signals)
        # No "RSI 62" or "RSI=62" style raw numbers
        import re
        assert not re.search(r"RSI[\s=:]\s*\d", out, re.IGNORECASE), (
            f"raw RSI value leaked into default output: {out!r}"
        )
        assert not re.search(r"SMA\d?[\s=:]\s*\d", out, re.IGNORECASE), (
            f"raw SMA value leaked into default output: {out!r}"
        )

    def test_verbose_output_includes_raw_indicator_values(self,
                                                        sample_ranked,
                                                        sample_pair_signals):
        """When ``verbose=True``, the output should expose the raw numbers
        (RSI, score) so power-users can see the underlying data."""
        out = advisor.explain_symbol("SCOM", ranked=sample_ranked,
                                     pair_signals=sample_pair_signals,
                                     verbose=True)
        # RSI 62 was set in the fixture — verbose mode should mention it
        # either as a number or in a structured "Raw:" footer
        import re
        has_rsi = re.search(r"62(\.\d+)?", out)
        assert has_rsi is not None, (
            f"verbose mode did not include raw RSI/indicator values: {out!r}"
        )


class TestDailyBrief:
    """advisor.daily_brief(...) → full Telegram brief under 4096 chars."""

    def test_returns_string(self, sample_ranked, sample_pair_signals):
        out = advisor.daily_brief(
            date="2026-06-28",
            ranked=sample_ranked,
            pair_signals=sample_pair_signals,
        )
        assert isinstance(out, str)
        assert len(out) > 0

    def test_fits_telegram(self, sample_ranked, sample_pair_signals):
        out = advisor.daily_brief(
            date="2026-06-28",
            ranked=sample_ranked,
            pair_signals=sample_pair_signals,
        )
        assert len(out) <= 4096

    def test_includes_date(self, sample_ranked, sample_pair_signals):
        out = advisor.daily_brief(
            date="2026-06-28",
            ranked=sample_ranked,
            pair_signals=sample_pair_signals,
        )
        assert "2026-06-28" in out

    def test_includes_top_opportunity_symbol(self, sample_ranked, sample_pair_signals):
        out = advisor.daily_brief(
            date="2026-06-28",
            ranked=sample_ranked,
            pair_signals=sample_pair_signals,
        )
        # SCOM is the top-ranked — must appear
        assert "SCOM" in out

    def test_includes_avoid_symbol(self, sample_ranked, sample_pair_signals):
        out = advisor.daily_brief(
            date="2026-06-28",
            ranked=sample_ranked,
            pair_signals=sample_pair_signals,
        )
        # ABSA is the bottom-ranked — must appear in warnings
        assert "ABSA" in out

    def test_handles_empty_pair_signals(self, sample_ranked):
        out = advisor.daily_brief(
            date="2026-06-28",
            ranked=sample_ranked,
            pair_signals={},
        )
        assert isinstance(out, str)
        assert len(out) > 0


class TestEnrichOpportunities:
    """advisor.enrich_opportunities(ranked) → narrative block."""

    def test_returns_string(self, sample_ranked):
        out = advisor.enrich_opportunities(sample_ranked, top_n=3)
        assert isinstance(out, str)

    def test_includes_top_symbols(self, sample_ranked):
        out = advisor.enrich_opportunities(sample_ranked, top_n=3)
        assert "SCOM" in out
        assert "KCB" in out

    def test_excludes_bottom_symbols(self, sample_ranked):
        out = advisor.enrich_opportunities(sample_ranked, top_n=3)
        # Top 3 are SCOM, KCB, EQTY — ABSA should NOT appear
        assert "ABSA" not in out


class TestEnrichWarnings:
    """advisor.enrich_warnings(ranked) → narrative block for worst assets."""

    def test_returns_string(self, sample_ranked):
        out = advisor.enrich_warnings(sample_ranked, top_n=3)
        assert isinstance(out, str)

    def test_includes_worst_symbols(self, sample_ranked):
        out = advisor.enrich_warnings(sample_ranked, top_n=3)
        # Bottom 3: ABSA, EABL, EQTY
        assert "ABSA" in out


class TestSummariseMarket:
    """advisor.summarise_market(ranked) → market summary block."""

    def test_returns_string(self, sample_ranked):
        out = advisor.summarise_market(sample_ranked)
        assert isinstance(out, str)
        assert "100" in out  # The opportunity score appears

    def test_empty_returns_neutral(self):
        out = advisor.summarise_market([])
        assert isinstance(out, str)


class TestPortfolio:
    """advisor.portfolio(ranked) → portfolio allocation block."""

    def test_returns_string(self, sample_ranked):
        out = advisor.portfolio(sample_ranked)
        assert isinstance(out, str)

    def test_includes_strong_symbols(self, sample_ranked):
        out = advisor.portfolio(sample_ranked)
        # Score >= 50 picks up SCOM, KCB, EQTY
        assert "SCOM" in out
        assert "KCB" in out

    def test_excludes_avoid_symbols(self, sample_ranked):
        out = advisor.portfolio(sample_ranked)
        # ABSA at 15 is below the 50 cutoff
        assert "ABSA" not in out
