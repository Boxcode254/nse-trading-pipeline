#!/usr/bin/env python3
"""Daily book-integrity assertion (auto-trader book vs AXYS official close).

Compares every held position in the auto-trader book (portfolio/state.json)
against the OFFICIAL AXYS NSE close (portfolio/axys_closes_<date>.json) and
alerts + logs any per-share price divergence beyond tolerance.

The auto-trader "book price" for a position is its marked value per share:
    book_price = position.current_value / position.shares
This is the per-share value the engine is carrying on the book, which must
tie out to the NSE official close (the AXYS tape) within tolerance.

TP-008 (2026-09-15): every run (including the no-source and error paths) also
writes the CURRENT status record ``portfolio/integrity_status.json`` via
:mod:`trading.services.integrity_status`.  The append-only incident log
(``portfolio/book_integrity.log``) is still written exactly as before, but the
daily report now reads the status record — a log tail is history, not status.

Exit codes:
    0 = book ties out (no divergence beyond tolerance)
    1 = divergence(s) flagged (and alerted/logged)
    2 = no official AXYS close available for today (stale/parse failure),
        or the book could not be read

The Telegram alert path is urllib straight to the API (LLM-independent) so a
real book-integrity problem reaches Emmanuel even when the agent layer is
down. Mirrors scripts/axys_reconcile.py:send_alert by design.
"""
from __future__ import annotations

import argparse
import sys
import os
import json
import datetime
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from trading.services import integrity_status as istat  # noqa: E402

PORTFOLIO_DIR = "/home/hermes/.trading/portfolio"
STATE_PATH = os.path.join(PORTFOLIO_DIR, "state.json")
TOL_PCT = 1.0  # book-vs-official divergence tolerance (%)
DEFAULT_CHAT = "293971003"
ENV_PATH = os.path.expanduser("~/.hermes/.env")
LOG_PATH = os.path.join(PORTFOLIO_DIR, "book_integrity.log")


# --- Direct Telegram alert path (LLM-independent) ---------------------------
# Same pattern as scripts/axys_reconcile.py:send_alert.
def _load_env() -> dict:
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def send_alert(title: str, body: str) -> bool:
    env = _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("CRON_ALERT_CHAT_ID") or env.get("CRON_ALERT_CHAT_ID", DEFAULT_CHAT)
    if not token:
        print("[book_integrity] TELEGRAM_BOT_TOKEN not available; cannot send alert",
              file=sys.stderr)
        return False
    text = f"\U0001f6a8 *Book-Integrity Alert*\n{title}\n\n```\n{body}\n```"
    payload = json.dumps({
        "chat_id": chat,
        "text": text,
        "parse_mode": "Markdown",
        "disable_notification": False,
    }).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if b'"ok":true' not in r.read():
                print("[book_integrity] telegram returned non-ok", file=sys.stderr)
                return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[book_integrity] telegram send failed: {e}", file=sys.stderr)
        return False


def load_axys_closes(date_str: str, portfolio_dir: str = PORTFOLIO_DIR):
    """Return (path, {sym: close}, date_words). Prefer today's file; fall back
    to the newest axys_closes file within the trailing 7 days."""
    preferred = os.path.join(portfolio_dir, f"axys_closes_{date_str}.json")
    if os.path.exists(preferred):
        path = preferred
    else:
        path = None
        for i in range(0, 8):
            d = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
            cand = os.path.join(portfolio_dir, f"axys_closes_{d}.json")
            if os.path.exists(cand):
                path = cand
                break
    if path is None:
        return "", {}, date_str
    with open(path) as f:
        data = json.load(f)
    closes = data.get("axys", {})
    date_words = data.get("date", date_str)
    return path, closes, date_words


def _log(divs, axys_path, date_words, log_path: str = LOG_PATH):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[{ts}] Book-integrity check -- {len(divs)} divergence(s) vs {os.path.basename(axys_path)} ({date_words})"]
    for sym, bp, off, d in divs:
        lines.append(f"  {sym}: book={bp:.2f} official={off} delta={d:+.2f}%")
    try:
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[book_integrity] log write failed: {e}", file=sys.stderr)


def _record(portfolio_dir, status, *, axys_path="", date_words=None,
            divergences=None, positions_checked=0, detail=None, dry_run=False):
    """Write (or, for a dry run, print) the current integrity status record."""
    if dry_run:
        return None
    try:
        return istat.record(
            portfolio_dir,
            status=status,
            source_file=os.path.basename(axys_path) if axys_path else None,
            source_date=date_words,
            positions_checked=positions_checked,
            tolerance_pct=TOL_PCT,
            divergences=divergences or [],
            detail=detail,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[book_integrity] status record write failed: {e}", file=sys.stderr)
        return None


def _divs_as_dicts(divs):
    return [
        {"symbol": sym, "book_price": round(bp, 4), "official": off, "delta_pct": round(d, 4)}
        for sym, bp, off, d in divs
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Book vs AXYS official close integrity check")
    ap.add_argument("--portfolio-dir", default=PORTFOLIO_DIR,
                    help="portfolio directory (default: %s)" % PORTFOLIO_DIR)
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and print only; never write the status record or the log")
    ap.add_argument("--no-alert", action="store_true", help="suppress the Telegram alert")
    ap.add_argument("--no-log", action="store_true", help="do not append to book_integrity.log")
    args = ap.parse_args(argv)

    portfolio_dir = args.portfolio_dir
    state_path = os.path.join(portfolio_dir, "state.json")
    log_path = os.path.join(portfolio_dir, "book_integrity.log")

    today = datetime.date.today()
    date_str = today.isoformat()

    try:
        with open(state_path) as f:
            book = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[book_integrity] cannot read book {state_path}: {e}", file=sys.stderr)
        _record(portfolio_dir, istat.STATUS_ERROR, detail=f"cannot read book: {e}",
                dry_run=args.dry_run)
        return 2

    axys_path, closes, date_words = load_axys_closes(date_str, portfolio_dir)
    if not closes:
        print(f"Book vs AXYS official close -- NO OFFICIAL SOURCE (tol {TOL_PCT:.1f}%)")
        print(f"official source: none found within 7d of {date_str}")
        _record(portfolio_dir, istat.STATUS_NO_SOURCE,
                detail=f"no official AXYS close within 7d of {date_str}",
                dry_run=args.dry_run)
        return 2

    divs = []
    checked = 0
    for pos in book.get("positions", []):
        sym = pos.get("symbol")
        shares = pos.get("shares", 0)
        cv = pos.get("current_value", 0.0)
        if not sym or shares <= 0:
            continue
        if sym not in closes:
            continue  # not on the official tape this session; skip
        checked += 1
        book_price = cv / shares
        official = closes[sym]
        delta = (book_price - official) / official * 100.0
        if abs(delta) >= TOL_PCT:
            divs.append((sym, book_price, official, delta))

    print(f"Book vs AXYS official close -- {len(divs)} divergence(s) (tol {TOL_PCT:.1f}%)")
    print(f"official source: {os.path.basename(axys_path)} ({date_words})")

    if divs:
        print(f"{'SYM':<11}{'BOOK':>8}{'OFFICIAL':>10}{'DELTA%':>9}")
        for sym, bp, off, d in divs:
            print(f"{sym:<11}{bp:>8.2f}{str(off):>10}{d:>8.2f}%")
        _record(portfolio_dir, istat.STATUS_DIVERGENCES, axys_path=axys_path,
                date_words=date_words, divergences=_divs_as_dicts(divs),
                positions_checked=checked, dry_run=args.dry_run)
        body = "\n".join(
            f"{s:<6} book={b:.2f} official={o} delta={d:+.2f}%" for s, b, o, d in divs
        )
        if not args.dry_run and not args.no_alert and send_alert(
                f"{len(divs)} book-vs-AXYS divergence(s)", body):
            print("[alert sent]")
        if not args.dry_run and not args.no_log:
            _log(divs, axys_path, date_words, log_path)
        return 1

    print("All held positions tie out within tolerance.")
    _record(portfolio_dir, istat.STATUS_OK, axys_path=axys_path, date_words=date_words,
            positions_checked=checked, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
