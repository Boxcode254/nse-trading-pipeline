#!/usr/bin/env python3
"""
News Store — tag, lookup, impact-flag, and digest business news.

Commands:
  add       Tag one article into the store
  lookup    Look up articles by ticker
  impact    Cross-reference articles against portfolio holdings
  digest    Generate a weekly digest
  list      List recent articles

Usage:
  python news_store.py add --headline "..." --tickers SCOM,KCB --source "Business Daily" [--summary "..."] [--tags earnings,regulatory]
  python news_store.py lookup SCOM [--limit 5]
  python news_store.py impact [--since 7d]
  python news_store.py digest [--since 7d] [--deliver]
  python news_store.py list [--limit 10]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

STORE_PATH = Path(__file__).resolve().parent.parent / "news" / "news_store.json"
PORTFOLIO_PATH = Path(__file__).resolve().parent.parent / "portfolio" / "mtm_state.json"

# ── helpers ──────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def load_store() -> dict:
    if not STORE_PATH.exists():
        return {"articles": [], "metadata": {"last_digest_sent": None}}
    with open(STORE_PATH) as f:
        return json.load(f)

def save_store(store: dict):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STORE_PATH, "w") as f:
        json.dump(store, f, indent=2)

def load_portfolio_holdings() -> set[str]:
    """Return set of ticker symbols currently held."""
    if not PORTFOLIO_PATH.exists():
        return set()
    try:
        with open(PORTFOLIO_PATH) as f:
            data = json.load(f)
        return {p["symbol"] for p in data.get("positions", [])}
    except (json.JSONDecodeError, KeyError):
        return set()

def parse_duration(dur: str) -> timedelta:
    """Parse '7d', '30d', '24h' into timedelta."""
    dur = dur.strip().lower()
    if dur.endswith("d"):
        return timedelta(days=int(dur[:-1]))
    elif dur.endswith("h"):
        return timedelta(hours=int(dur[:-1]))
    elif dur.endswith("w"):
        return timedelta(weeks=int(dur[:-1]))
    else:
        return timedelta(days=7)  # default

# ── commands ─────────────────────────────────────────────────────────

def cmd_add(args):
    """Tag one or more articles into the store."""
    store = load_store()
    tickers = [t.upper().strip() for t in args.tickers.split(",") if t.strip()]
    tags = [t.strip().lower() for t in args.tags.split(",") if t.strip()] if args.tags else []
    holdings = load_portfolio_holdings()

    # Check portfolio overlap
    affected = [t for t in tickers if t in holdings]
    impact = None
    impact_reasoning = None
    if affected:
        impact = args.impact or "neutral"
        impact_reasoning = f"Portfolio holding(s): {', '.join(affected)}"

    article = {
        "id": now_iso().replace(":", "").replace("-", "").replace("T", "_"),
        "date": today_str(),
        "datetime": now_iso(),
        "headline": args.headline,
        "source": args.source,
        "summary": args.summary or "",
        "tickers": tickers,
        "portfolio_affected": bool(affected),
        "impact": impact,
        "impact_reasoning": impact_reasoning,
        "tags": tags,
    }
    store["articles"].insert(0, article)
    save_store(store)
    tags_str = ", ".join(tickers) if tickers else "(no tickers)"
    print(f"✓ Tagged: {args.headline[:60]}")
    print(f"  Tickers: {tags_str}")
    if affected:
        print(f"  ⚡ PORTFOLIO IMPACT: {', '.join(affected)} — {impact}")
    return article

def cmd_lookup(args):
    """Look up articles by ticker."""
    store = load_store()
    target = args.ticker.upper().strip()
    matches = [a for a in store["articles"] if target in a.get("tickers", [])]
    matches = matches[: args.limit]
    if not matches:
        print(f"No articles found for {target}")
        return
    print(f"📰 {len(matches)} article(s) for {target}:\n")
    for a in matches:
        pf = " ⚡ PORTFOLIO" if a.get("portfolio_affected") else ""
        imp = f" [{a.get('impact','?')}]" if a.get("impact") else ""
        print(f"  {a['date']} {pf}{imp}")
        print(f"  {a['headline']}")
        if a.get("summary"):
            print(f"  {a['summary'][:120]}")
        print(f"  source: {a['source']}  |  tags: {', '.join(a.get('tags',[]))}")
        print()

def cmd_impact(args):
    """Cross-reference recent articles against current portfolio."""
    store = load_store()
    holdings = load_portfolio_holdings()
    since = parse_duration(args.since)
    cutoff = datetime.now(timezone.utc) - since

    affected = []
    for a in store["articles"]:
        try:
            adt = datetime.strptime(a["datetime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            adt = datetime.now(timezone.utc)
        if adt < cutoff:
            continue
        if a.get("portfolio_affected"):
            affected.append(a)

    if not affected:
        print(f"No portfolio-impacting articles in the last {args.since}")
        return

    print(f"⚡ Portfolio Impact — last {args.since}:\n")
    for a in affected:
        imp = a.get("impact", "?")
        print(f"  {a['date']} [{imp.upper()}] {a['headline'][:70]}")
        held = [t for t in a.get("tickers", []) if t in holdings]
        print(f"  Holdings: {', '.join(held)}")
        if a.get("impact_reasoning"):
            print(f"  Why: {a['impact_reasoning']}")
        print()

def cmd_digest(args):
    """Generate a digest of recent articles."""
    store = load_store()
    since = parse_duration(args.since)
    cutoff = datetime.now(timezone.utc) - since
    holdings = load_portfolio_holdings()

    articles = []
    for a in store["articles"]:
        try:
            adt = datetime.strptime(a["datetime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            adt = datetime.now(timezone.utc)
        if adt >= cutoff:
            articles.append(a)

    if not articles:
        print(f"No articles in the last {args.since}")
        return

    # Count portfolio-relevant items
    pf_count = sum(1 for a in articles if a.get("portfolio_affected"))

    print(f"📰 Weekly News Digest — last {args.since}")
    print(f"   {len(articles)} articles, {pf_count} portfolio-relevant\n")

    # Group by date
    by_date: dict[str, list] = {}
    for a in articles:
        by_date.setdefault(a["date"], []).append(a)

    for date in sorted(by_date.keys(), reverse=True):
        print(f"── {date} ──")
        for a in by_date[date]:
            pf = "⚡" if a.get("portfolio_affected") else " "
            tick = ", ".join(a.get("tickers", []))
            print(f"  {pf} {a['headline'][:80]}")
            if tick:
                print(f"     Tickers: {tick}")
        print()

    # If this is a delivery run, update last_digest_sent
    if args.deliver:
        store["metadata"]["last_digest_sent"] = now_iso()
        save_store(store)
        print(f"(Digest timestamp saved)")

def cmd_list(args):
    """List recent articles."""
    store = load_store()
    articles = store["articles"][: args.limit]
    if not articles:
        print("No articles in store")
        return
    print(f"📰 {len(articles)} most recent articles:\n")
    for a in articles:
        tick = ", ".join(a.get("tickers", [])) or "-"
        pf = "⚡" if a.get("portfolio_affected") else " "
        print(f"  {pf} [{a['date']}] {a['headline'][:70]}")
        print(f"     {a['source']} | {tick}")
        print()

# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="News Store — tag, lookup, and digest")
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="Tag an article")
    p_add.add_argument("--headline", required=True)
    p_add.add_argument("--tickers", default="", help="Comma-separated, e.g. SCOM,KCB")
    p_add.add_argument("--source", default="Business Daily")
    p_add.add_argument("--summary", default="")
    p_add.add_argument("--tags", default="")
    p_add.add_argument("--impact", choices=["positive", "negative", "neutral"], default=None)

    # lookup
    p_lookup = sub.add_parser("lookup", help="Look up articles by ticker")
    p_lookup.add_argument("ticker", help="Ticker symbol")
    p_lookup.add_argument("--limit", type=int, default=10)

    # impact
    p_impact = sub.add_parser("impact", help="Portfolio cross-reference")
    p_impact.add_argument("--since", default="7d")

    # digest
    p_digest = sub.add_parser("digest", help="Generate digest")
    p_digest.add_argument("--since", default="7d")
    p_digest.add_argument("--deliver", action="store_true", help="Save delivery timestamp")

    # list
    p_list = sub.add_parser("list", help="List recent articles")
    p_list.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    commands = {
        "add": cmd_add,
        "lookup": cmd_lookup,
        "impact": cmd_impact,
        "digest": cmd_digest,
        "list": cmd_list,
    }
    commands[args.command](args)

if __name__ == "__main__":
    main()
