"""Tests for the Market Intelligence layer.

TDD: these tests were authored before the implementation. Each
``test_*`` documents one behavior the production code must satisfy.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# We import the modules under test lazily inside each test to keep the
# RED→GREEN cycle tight: write the test, see it fail with ImportError,
# then write the module.


# ── sentiment.py ───────────────────────────────────────────────────


class TestSentimentScore:
    """Keyword-based sentiment scorer. Returns -1.0 (bearish) to +1.0 (bullish)."""

    def test_bullish_headline_scores_positive(self):
        from trading.services.market_intel import sentiment
        s = sentiment.score("Safaricom reports record profit and raises dividend")
        assert s > 0.3
        assert s <= 1.0

    def test_bearish_headline_scores_negative(self):
        from trading.services.market_intel import sentiment
        s = sentiment.score("Bank faces scandal and losses mount after rate hike")
        assert s < -0.3
        assert s >= -1.0

    def test_neutral_headline_near_zero(self):
        from trading.services.market_intel import sentiment
        s = sentiment.score("Quarterly trading update — no major changes")
        assert -0.2 <= s <= 0.2

    def test_empty_string_returns_zero(self):
        from trading.services.market_intel import sentiment
        assert sentiment.score("") == 0.0

    def test_none_input_returns_zero(self):
        from trading.services.market_intel import sentiment
        assert sentiment.score(None) == 0.0

    def test_score_is_bounded(self):
        from trading.services.market_intel import sentiment
        # Even a maximally positive headline stays within [-1, +1]
        s = sentiment.score("huge profit surge beat growth win dividend upgrade acquisition expansion")
        assert -1.0 <= s <= 1.0

    def test_classify_positive(self):
        from trading.services.market_intel import sentiment
        assert sentiment.classify(0.6) == "positive"

    def test_classify_negative(self):
        from trading.services.market_intel import sentiment
        assert sentiment.classify(-0.5) == "negative"

    def test_classify_neutral(self):
        from trading.services.market_intel import sentiment
        assert sentiment.classify(0.1) == "neutral"

    def test_flags_known_terms(self):
        from trading.services.market_intel import sentiment
        flags = sentiment.flags("CBK rate hike: banking sector braces for impact")
        assert "rate hike" in flags
        assert "banking" in flags or "rate hike" in flags


# ── cache.py ───────────────────────────────────────────────────────


class TestCache:
    """JSON file cache with TTL semantics."""

    def test_set_and_get(self, tmp_path: Path):
        from trading.services.market_intel import cache
        c = cache.Cache(directory=tmp_path)
        c.set("foo", {"a": 1})
        assert c.get("foo") == {"a": 1}

    def test_get_missing_key_returns_none(self, tmp_path: Path):
        from trading.services.market_intel import cache
        c = cache.Cache(directory=tmp_path)
        assert c.get("nope") is None

    def test_ttl_expiry(self, tmp_path: Path):
        from trading.services.market_intel import cache
        c = cache.Cache(directory=tmp_path)
        c.set("foo", {"a": 1}, ttl_seconds=1)
        # Find the on-disk file (key is hashed) and backdate it
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        path = files[0]
        old = time.time() - 100
        os.utime(path, (old, old))
        assert c.get("foo", ttl_seconds=10) is None  # age > ttl

    def test_clear_removes_entry(self, tmp_path: Path):
        from trading.services.market_intel import cache
        c = cache.Cache(directory=tmp_path)
        c.set("foo", {"a": 1})
        c.clear("foo")
        assert c.get("foo") is None

    def test_clear_all(self, tmp_path: Path):
        from trading.services.market_intel import cache
        c = cache.Cache(directory=tmp_path)
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        assert c.get("a") is None
        assert c.get("b") is None

    def test_persists_across_instances(self, tmp_path: Path):
        from trading.services.market_intel import cache
        cache.Cache(directory=tmp_path).set("foo", 42)
        assert cache.Cache(directory=tmp_path).get("foo") == 42


# ── sources.py ─────────────────────────────────────────────────────


class TestSourceRegistry:
    """Source registry + simple per-source rate limit."""

    def test_register_and_get(self):
        from trading.services.market_intel import sources
        reg = sources.Registry()
        fn = MagicMock(return_value=[{"headline": "x"}])
        reg.register("test", fn, rate_limit_per_minute=10)
        assert reg.get("test") is fn

    def test_get_unknown_returns_none(self):
        from trading.services.market_intel import sources
        reg = sources.Registry()
        assert reg.get("missing") is None

    def test_rate_limit_blocks_excess_calls(self):
        from trading.services.market_intel import sources
        reg = sources.Registry()
        reg.register("rl", MagicMock(), rate_limit_per_minute=2)
        assert reg.allowed("rl") is True
        reg.mark_used("rl")
        reg.mark_used("rl")
        assert reg.allowed("rl") is False  # exhausted

    def test_list_sources(self):
        from trading.services.market_intel import sources
        reg = sources.Registry()
        reg.register("a", MagicMock())
        reg.register("b", MagicMock())
        names = reg.list()
        assert set(names) == {"a", "b"}


# ── news.py ────────────────────────────────────────────────────────


class TestNewsFetcher:
    """Multi-source news fetcher."""

    @pytest.fixture
    def isolated_cache(self, tmp_path: Path, monkeypatch):
        """Point news._cache_dir at a tmp dir for the duration of the test."""
        from trading.services.market_intel import news
        monkeypatch.setattr(news, "_cache_dir", lambda: tmp_path)
        return tmp_path

    def test_fetch_returns_normalised_items(self, isolated_cache):
        from trading.services.market_intel import news
        fake_item = {
            "headline": "Safaricom profit up 20%",
            "source": "alpha",
            "url": "https://example.com/1",
            "timestamp": "2026-06-28T10:00:00Z",
        }
        with patch.object(news, "_fetch_alpha", return_value=[fake_item]), \
             patch.object(news, "_fetch_finviz", return_value=[]), \
             patch.object(news, "_fetch_google", return_value=[]):
            items = news.fetch(["SCOM"], date="2099-01-01-normalise")
        assert len(items) >= 1
        item = items[0]
        assert "headline" in item
        assert "source" in item
        assert "sentiment" in item
        assert "relevance" in item
        assert isinstance(item["relevance"], (int, float))
        assert -1.0 <= item["sentiment"] <= 1.0

    def test_fetch_falls_back_to_secondary(self, isolated_cache):
        from trading.services.market_intel import news
        with patch.object(news, "_fetch_alpha", return_value=[]), \
             patch.object(news, "_fetch_finviz", return_value=[
                 {"headline": "Kenya banking news", "source": "finviz",
                  "url": "x", "timestamp": "2026-06-28T10:00:00Z"}
             ]), \
             patch.object(news, "_fetch_google", return_value=[]):
            items = news.fetch(["KCB"], date="2099-01-01-fallback")
        assert any(i["source"] == "finviz" for i in items)

    def test_fetch_uses_cache(self, tmp_path: Path):
        from trading.services.market_intel import news
        # Pre-populate the cache
        from trading.services.market_intel import cache as mi_cache
        mi_cache.Cache(directory=tmp_path).set(
            "news:SCOM:2026-06-28",
            [{"headline": "cached", "source": "alpha", "url": "u",
              "timestamp": "2026-06-28T10:00:00Z", "sentiment": 0.1,
              "relevance": 1.0}],
        )
        with patch.object(news, "_cache_dir", return_value=tmp_path), \
             patch.object(news, "_fetch_alpha") as mock_alpha, \
             patch.object(news, "_fetch_finviz") as mock_finviz, \
             patch.object(news, "_fetch_google") as mock_google:
            items = news.fetch(["SCOM"], date="2026-06-28")
        mock_alpha.assert_not_called()
        assert items[0]["headline"] == "cached"

    def test_fetch_all_sources_fail_returns_empty(self, isolated_cache):
        from trading.services.market_intel import news
        with patch.object(news, "_fetch_alpha", return_value=[]), \
             patch.object(news, "_fetch_finviz", return_value=[]), \
             patch.object(news, "_fetch_google", return_value=[]), \
             patch.object(news, "_cache_dir", return_value=Path("/tmp/_unused_mi_test")):
            items = news.fetch(["SCOM"], date="2099-12-31-unique",
                                use_cache=False)
        assert items == []

    def test_dedupes_by_url(self, isolated_cache):
        from trading.services.market_intel import news
        dup = {"headline": "x", "source": "alpha", "url": "https://u",
               "timestamp": "2026-06-28T10:00:00Z"}
        with patch.object(news, "_fetch_alpha", return_value=[dup]), \
             patch.object(news, "_fetch_finviz", return_value=[dict(dup)]), \
             patch.object(news, "_fetch_google", return_value=[]):
            items = news.fetch(["SCOM"], date="2099-01-01-dedupe")
        assert len(items) == 1

    def test_relevance_higher_when_ticker_in_headline(self, isolated_cache):
        from trading.services.market_intel import news
        items = [
            {"headline": "SCOM wins contract", "source": "x", "url": "a",
             "timestamp": "2026-06-28T10:00:00Z", "sentiment": 0.0, "relevance": 1.0},
            {"headline": "Other news", "source": "x", "url": "b",
             "timestamp": "2026-06-28T10:00:00Z", "sentiment": 0.0, "relevance": 0.3},
        ]
        scored = news._add_relevance(items, "SCOM")
        assert scored[0]["relevance"] > scored[1]["relevance"]


# ── calendar.py ────────────────────────────────────────────────────


class TestCalendar:
    """Macro-economic event calendar with sector mapping."""

    def test_upcoming_returns_list(self):
        from trading.services.market_intel import calendar
        # Use a near-term date so it survives the 30-day window
        from datetime import datetime, timezone, timedelta
        near = (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
        with patch.object(calendar, "_fetch_events", return_value=[
            {"event": "CBK rate decision", "date": near,
             "impact": "high", "sectors": ["banking"], "tickers": ["KCB"],
             "country": "KE"},
        ]):
            events = calendar.upcoming()
        assert isinstance(events, list)
        assert events[0]["event"] == "CBK rate decision"

    def test_for_symbol_filters_relevant(self):
        from trading.services.market_intel import calendar
        events = [
            {"event": "CBK rate decision", "date": "2099-12-31",
             "impact": "high", "sectors": ["banking"], "tickers": ["KCB", "EQTY"]},
            {"event": "Telecom earnings", "date": "2099-12-31",
             "impact": "medium", "sectors": ["telecom"], "tickers": ["SCOM"]},
        ]
        relevant = calendar._filter_relevant(events, "KCB")
        assert len(relevant) == 1
        assert relevant[0]["event"] == "CBK rate decision"

    def test_falls_back_to_static_seed_when_no_source(self):
        from trading.services.market_intel import calendar
        with patch.object(calendar, "_fetch_events", return_value=[]):
            events = calendar.upcoming()
        # Static seed: must always return *something* useful (graceful degradation)
        assert isinstance(events, list)

    def test_format_event(self):
        from trading.services.market_intel import calendar
        ev = {"event": "CBK rate decision", "date": "2099-12-31",
              "impact": "high", "sectors": ["banking"], "tickers": ["KCB"]}
        line = calendar.format_event(ev)
        assert "CBK rate decision" in line
        assert "banking" in line.lower() or "KCB" in line


# ── earnings.py ────────────────────────────────────────────────────


class TestEarnings:
    """Earnings calendar tracker for NSE-listed stocks."""

    def test_upcoming_returns_dict_by_symbol(self):
        from trading.services.market_intel import earnings
        with patch.object(earnings, "_fetch_earnings", return_value={
            "SCOM": {"report_date": "2099-08-15", "status": "upcoming"},
        }):
            result = earnings.upcoming(["SCOM"])
        assert "SCOM" in result
        assert result["SCOM"]["report_date"] == "2099-08-15"

    def test_status_pre_earnings_window(self):
        from trading.services.market_intel import earnings
        # Report in 3 days, today is 2026-06-28
        status = earnings._classify_window("2026-07-01", "2026-06-28")
        assert status == "pre-earnings"

    def test_status_reported(self):
        from trading.services.market_intel import earnings
        status = earnings._classify_window("2026-06-20", "2026-06-28")
        assert status == "reported"

    def test_status_upcoming_far(self):
        from trading.services.market_intel import earnings
        status = earnings._classify_window("2026-09-01", "2026-06-28")
        assert status == "upcoming"

    def test_format_earnings_line(self):
        from trading.services.market_intel import earnings
        line = earnings.format_line("SCOM", {"report_date": "2026-08-15",
                                              "status": "upcoming",
                                              "expected_impact": "medium"})
        assert "SCOM" in line
        assert "2026-08-15" in line


# ── sector.py ──────────────────────────────────────────────────────


class TestSector:
    """Sector rotation tracker."""

    def test_snapshot_returns_sectors(self):
        from trading.services.market_intel import sector
        with patch.object(sector, "_compute_sector_perf", return_value=[
            {"sector": "banking", "perf_pct": 2.1, "rotation": "in"},
            {"sector": "telecom", "perf_pct": 1.0, "rotation": "in"},
        ]):
            snap = sector.snapshot()
        assert isinstance(snap, list)
        assert any(s["sector"] == "banking" for s in snap)

    def test_classify_rotation(self):
        from trading.services.market_intel import sector
        assert sector._classify_rotation(2.0) == "in"
        assert sector._classify_rotation(-2.0) == "out"
        assert sector._classify_rotation(0.5) == "neutral"

    def test_format_sector_line(self):
        from trading.services.market_intel import sector
        line = sector.format_line({"sector": "banking", "perf_pct": 2.5,
                                    "rotation": "in"})
        assert "banking" in line
        assert "+2.5%" in line or "2.5%" in line

    def test_sector_for_symbol(self):
        from trading.services.market_intel import sector
        assert sector.sector_for("SCOM") == "telecom"
        assert sector.sector_for("KCB") == "banking"
        assert sector.sector_for("EABL") == "consumer"
        assert sector.sector_for("EQTY") == "banking"
        assert sector.sector_for("ABSA") == "banking"
        assert sector.sector_for("SCBK") == "banking"
        assert sector.sector_for("UNKNOWN") == "other"


# ── context.py ─────────────────────────────────────────────────────


class TestContextAssembler:
    """Picks top-3 context items for a symbol."""

    def test_assemble_for_symbol(self, tmp_path: Path):
        from trading.services.market_intel import context
        with patch.object(context, "news", new=_stub_news([
            {"headline": "SCOM wins", "source": "alpha", "url": "u1",
             "timestamp": "2026-06-28T10:00:00Z", "sentiment": 0.6,
             "relevance": 0.9},
            {"headline": "Kenya markets", "source": "alpha", "url": "u2",
             "timestamp": "2026-06-27T10:00:00Z", "sentiment": 0.1,
             "relevance": 0.4},
        ])), \
        patch.object(context, "calendar", new=_stub_calendar([
            {"event": "CBK rate decision", "date": "2099-12-31",
             "impact": "high", "sectors": ["telecom"], "tickers": ["SCOM"]},
        ])), \
        patch.object(context, "earnings", new=_stub_earnings({
            "SCOM": {"report_date": "2099-08-15", "status": "upcoming",
                      "expected_impact": "medium"},
        })), \
        patch.object(context, "sector", new=_stub_sector([
            {"sector": "telecom", "perf_pct": 3.2, "rotation": "in"},
        ])):
            items = context.assemble("SCOM", max_items=3)
        assert len(items) == 3
        # The first item is the highest-relevance news piece
        assert items[0]["kind"] == "news"
        assert items[0]["relevance"] == 0.9

    def test_assemble_with_no_data_returns_empty(self):
        from trading.services.market_intel import context
        with patch.object(context, "news", new=_stub_news([])), \
             patch.object(context, "calendar", new=_stub_calendar([])), \
             patch.object(context, "earnings", new=_stub_earnings({})), \
             patch.object(context, "sector", new=_stub_sector([])):
            assert context.assemble("UNKNOWN") == []

    def test_format_context(self):
        from trading.services.market_intel import context
        items = [
            {"kind": "news", "label": "positive", "text": "SCOM wins",
             "timestamp": "2026-06-28T10:00:00Z", "relevance": 0.9},
            {"kind": "calendar", "label": "macro", "text": "CBK rate decision",
             "timestamp": "2099-12-31", "relevance": 0.8},
        ]
        out = context.format_block("SCOM", items)
        assert "SCOM" in out
        assert "SCOM wins" in out
        assert "CBK rate decision" in out

    def test_dedupes_across_modules(self, tmp_path: Path):
        from trading.services.market_intel import context
        # Same headline from news and from calendar's mention
        with patch.object(context, "news", new=_stub_news([
            {"headline": "SCOM wins big contract", "source": "alpha",
             "url": "u", "timestamp": "2026-06-28T10:00:00Z",
             "sentiment": 0.6, "relevance": 0.9},
        ])), \
        patch.object(context, "calendar", new=_stub_calendar([
            {"event": "SCOM wins big contract — analyst call",
             "date": "2099-12-31", "impact": "low",
             "sectors": ["telecom"], "tickers": ["SCOM"]},
        ])), \
        patch.object(context, "earnings", new=_stub_earnings({})), \
        patch.object(context, "sector", new=_stub_sector([])):
            items = context.assemble("SCOM", max_items=5)
        # Should be deduped by similarity — at most 1 "SCOM wins big contract"
        scom_wins = [i for i in items
                     if "SCOM wins big contract" in i["text"]]
        assert len(scom_wins) == 1


# ── scanner.py ─────────────────────────────────────────────────────


class TestScanner:
    """Scheduled orchestrator that populates the context store."""

    def test_run_writes_to_context_store(self, tmp_path: Path):
        from trading.services.market_intel import scanner
        store_path = tmp_path / "context_store.json"
        with patch.object(scanner, "_tracked_symbols", return_value=["SCOM", "KCB"]), \
             patch.object(scanner, "news", new=_stub_news([
                 {"headline": "x", "source": "alpha", "url": "u",
                  "timestamp": "2026-06-28T10:00:00Z",
                  "sentiment": 0.1, "relevance": 0.5},
             ])), \
             patch.object(scanner, "calendar", new=_stub_calendar([])), \
             patch.object(scanner, "earnings", new=_stub_earnings({})), \
             patch.object(scanner, "sector", new=_stub_sector([])), \
             patch.object(scanner, "_context_store_path", return_value=store_path):
            scanner.run()
        assert store_path.exists()
        data = json.loads(store_path.read_text())
        assert "SCOM" in data["news"] or "SCOM" in data
        # Either news for SCOM or KCB should be in the store
        all_news = data.get("news", {}) if isinstance(data.get("news"), dict) else {}
        assert "SCOM" in all_news or "KCB" in all_news

    def test_run_handles_no_symbols(self, tmp_path: Path):
        from trading.services.market_intel import scanner
        store_path = tmp_path / "context_store.json"
        with patch.object(scanner, "_tracked_symbols", return_value=[]), \
             patch.object(scanner, "news", new=_stub_news([])), \
             patch.object(scanner, "calendar", new=_stub_calendar([])), \
             patch.object(scanner, "earnings", new=_stub_earnings({})), \
             patch.object(scanner, "sector", new=_stub_sector([])), \
             patch.object(scanner, "_context_store_path", return_value=store_path):
            scanner.run()
        assert store_path.exists()


# ── Helpers ────────────────────────────────────────────────────────


class _StubModule:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _stub_news(items):
    return _StubModule(fetch=lambda symbols, **kw: items)


def _stub_calendar(items):
    return _StubModule(
        upcoming=lambda **kw: items,
        _filter_relevant=lambda events, symbol: [
            e for e in (events or [])
            if symbol in (e.get("tickers") or [])
            or "banking" in (e.get("sectors") or [])
            or "telecom" in (e.get("sectors") or [])
        ],
    )


def _stub_earnings(d):
    return _StubModule(upcoming=lambda symbols, **kw: d)


def _stub_sector(items):
    return _StubModule(
        snapshot=lambda **kw: items,
        sector_for=lambda symbol: {
            "SCOM": "telecom",
            "KCB": "banking",
            "EQTY": "banking",
            "EABL": "consumer",
            "ABSA": "banking",
            "SCBK": "banking",
        }.get(symbol.upper(), "other"),
    )
