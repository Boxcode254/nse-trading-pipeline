"""Multi-source news fetcher.

The high-level ``fetch`` tries each registered source in order
(primary → secondary → tertiary), merges the results, dedupes by
URL, scores relevance per requested symbol, and caches the merged
result for ``TTL`` hours.

The default path — with no API keys configured — returns an empty
list. That's the graceful-degradation requirement: the advisor
should still work, just without context. In practice, the scanner
cron will populate the cache from real sources on the first run.

Source priority
---------------

1. **Alpha Vantage News** — primary when ``ALPHA_VANTAGE_API_KEY`` is set.
2. **Finviz RSS** — free, no auth, no rate limit. Scrapes the
   per-ticker RSS feed. Stubbed for the offline default (returns []).
3. **Google News RSS** — free, no auth. Stubbed for offline.

In the test suite, all three are patched. In production without
keys, only the Finviz / Google stubs run (and return []), so
``fetch`` returns [] and the cache stays cold until a future
deployment wires up real keys.

Each source returns a list of dicts with the shape::

    {headline, source, url, timestamp, ...}

The fetcher then enriches each item with ``sentiment`` and
``relevance`` and dedupes by URL.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote_plus

from . import cache as mi_cache
from . import sentiment
from .sources import registry as source_registry

# Where to keep the on-disk cache. We use the project data dir so
# the cache survives across runs.
_HOME = Path(os.path.expanduser("~/.trading"))
_DEFAULT_CACHE_DIR = _HOME / "data" / "market_intel_cache"


def _cache_dir() -> Path:
    return _DEFAULT_CACHE_DIR


# ── Public API ────────────────────────────────────────────────────


def fetch(
    symbols: Iterable[str],
    *,
    date: Optional[str] = None,
    use_cache: bool = True,
    cache_ttl_seconds: int = 4 * 3600,  # 4h per spec
) -> list[dict]:
    """Return a list of news items for the requested symbols.

    Each item is a dict::

        {
            "headline":  str,
            "source":    str,   # "alpha" | "finviz" | "google" | "calendar"
            "url":       str,
            "timestamp": str,   # ISO 8601
            "sentiment": float, # -1.0 .. +1.0
            "relevance": float, # 0.0 .. 1.0 (highest = most on-topic)
        }

    The function is best-effort: it tries every source, caches the
    merged result, and returns an empty list if every source
    failed or no API keys are configured.
    """
    symbols = list(symbols) if symbols else []
    if not symbols:
        return []

    date = date or _today()
    cache_key = f"news:{','.join(sorted(symbols))}:{date}"
    c = mi_cache.Cache(directory=_cache_dir())

    if use_cache:
        cached = c.get(cache_key, ttl_seconds=cache_ttl_seconds)
        if cached is not None:
            return cached

    # Try each source in priority order. A source that returns []
    # is treated as "no data" — we move on. A source that raises
    # is also moved past silently (graceful degradation).
    items: list[dict] = []
    seen_urls: set[str] = set()

    for source_name in ("alpha", "finviz", "google"):
        try:
            if not source_registry.allowed(source_name):
                continue
            raw = _call_source(source_name, symbols)
            source_registry.mark_used(source_name)
        except Exception:  # noqa: BLE001
            continue
        for raw_item in raw or []:
            if not isinstance(raw_item, dict):
                continue
            url = raw_item.get("url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            items.append({
                "headline": raw_item.get("headline", ""),
                "source": raw_item.get("source", source_name),
                "url": url,
                "timestamp": raw_item.get("timestamp", _now_iso()),
                "sentiment": sentiment.score(raw_item.get("headline", "")),
                "relevance": 0.0,  # filled in below
            })

    # Score relevance per symbol — average across all requested
    # symbols so a multi-ticker query stays balanced.
    for item in items:
        item["relevance"] = _relevance_for(item, symbols)

    # Sort by relevance desc, then timestamp desc.
    items.sort(key=lambda i: (i.get("relevance", 0.0), i.get("timestamp", "")),
               reverse=True)

    # Cache the merged result.
    try:
        c.set(cache_key, items, ttl_seconds=cache_ttl_seconds)
    except Exception:  # noqa: BLE001
        pass  # cache failure is non-fatal

    return items


# ── Source backends (each returns list of raw dicts) ──────────────


def _call_source(name: str, symbols: list[str]) -> list[dict]:
    """Dispatch to a registered source function by name."""
    fn = source_registry.get(name)
    if fn is None:
        # Fall through to the built-in stubs so the test suite and
        # offline default still work.
        if name == "alpha":
            return _fetch_alpha(symbols)
        if name == "finviz":
            return _fetch_finviz(symbols)
        if name == "google":
            return _fetch_google(symbols)
        return []
    return list(fn(symbols) or [])


def _fetch_alpha(symbols: list[str]) -> list[dict]:
    """Alpha Vantage News & Sentiments API.

    Requires ``ALPHA_VANTAGE_API_KEY``. Without it, returns [].
    Free tier: 5 calls/min, 500/day.
    """
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        import urllib.request
        import urllib.parse
        tickers = ",".join(symbols)
        params = urllib.parse.urlencode({
            "function": "NEWS_SENTIMENT",
            "tickers": tickers,
            "apikey": api_key,
            "limit": "20",
        })
        url = f"https://www.alphavantage.co/query?{params}"
        with urllib.request.urlopen(url, timeout=10) as resp:  # nosec
            data = json.loads(resp.read().decode("utf-8"))
        feed = data.get("feed") or []
        out = []
        for entry in feed:
            out.append({
                "headline": entry.get("title", ""),
                "source": "alpha",
                "url": entry.get("url", ""),
                "timestamp": entry.get("time_published", ""),
            })
        return out
    except Exception:  # noqa: BLE001
        return []


def _fetch_finviz(symbols: list[str]) -> list[dict]:
    """Finviz per-ticker RSS feed (no auth, no key).

    Stub in the offline default. Real implementation would parse
    ``https://finviz.com/quote.ashx?t={SYMBOL}&ty=csv`` or the
    news RSS feed. Kept as a stub so the test suite doesn't hit
    the network and the offline deployment works.
    """
    return []


def _fetch_google(symbols: list[str]) -> list[dict]:
    """Google News RSS (no auth, no key).

    Stub in the offline default. Real implementation would parse
    ``https://news.google.com/rss/search?q={SYMBOL}`` and convert
    the XML entries to our shape. Same reasoning as Finviz: no
    network in tests, and graceful degradation offline.
    """
    return []


# ── Helpers ────────────────────────────────────────────────────────


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _now_iso() -> str:
    # ISO 8601 UTC, second precision (matches Alpha Vantage shape).
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_relevance(items: list[dict], symbol: str) -> list[dict]:
    """Public helper: set ``relevance`` on each item for one symbol.

    Useful in tests and when callers want to re-score after merging
    sources. The private ``_relevance_for`` is the single source of
    truth; this just loops over a list.
    """
    for item in items:
        item["relevance"] = _relevance_for(item, [symbol])
    return items


def _relevance_for(item: dict, symbols: list[str]) -> float:
    """Score how relevant a single item is to the requested symbols.

    Heuristics (in order of weight):

    - Ticker symbol appears in the headline → 0.6
    - Ticker is mentioned in any ``tickers`` field (Alpha Vantage) → 0.5
    - Headline contains a sector keyword relevant to the symbol → 0.3
    - Otherwise → 0.2 baseline
    """
    headline = (item.get("headline") or "").lower()
    tickers_field = " ".join(item.get("tickers") or []).lower()
    if not headline and not tickers_field:
        return 0.0

    score = 0.0
    for sym in symbols:
        s = sym.lower()
        # Direct ticker mention in the headline is the strongest signal.
        if s in headline:
            score = max(score, 0.6)
        elif tickers_field and s in tickers_field:
            score = max(score, 0.5)
        # Sector keywords (only checked if no direct match)
        if score < 0.4:
            for kw, weight in _SECTOR_KEYWORDS.get(s, []):
                if kw in headline:
                    score = max(score, weight)
    return max(score, 0.2)  # baseline


# Map symbols to (sector_keyword, weight) hints. A weak signal — most
# relevance comes from direct ticker mention. Kept small to stay
# honest.
_SECTOR_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "scom": [("telecom", 0.3), ("5g", 0.3), ("m-pesa", 0.35), ("safaricom", 0.4)],
    "kcb": [("banking", 0.3), ("kcb", 0.4), ("loan", 0.2), ("deposit", 0.2)],
    "eqty": [("banking", 0.3), ("equity", 0.4), ("loan", 0.2)],
    "eabl": [("brewer", 0.4), ("beer", 0.35), ("diageo", 0.3), ("consumer", 0.25)],
    "absa": [("banking", 0.3), ("absa", 0.4), ("loan", 0.2)],
    "scbk": [("banking", 0.3), ("standard chartered", 0.4), ("loan", 0.2)],
    "ctum": [("centum", 0.45), ("investment", 0.25)],
}
