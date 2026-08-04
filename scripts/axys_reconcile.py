#!/usr/bin/env python3
"""AXYS Market Pulse reconciliation against live MTM.

Given a Daily_Market_Watch / AXYS Market Pulse PDF (forwarded by Kratos):
  1. Extract text (pdftotext -layout).
  2. Parse per-share closing prices from the price tables -- these are
     attributed "Source: NSE, AIB-AXYS Research" -> NSE official tape,
     treated as GROUND TRUTH.
  3. Parse per-share day-direction from the Equities Highlights narrative
     ("... such as X, Y, Z, which gained A%, B%, C% respectively").
  4. Load current MTM (mtm_state.json) for held positions.
  5. Diff AXYS NSE-sourced close vs MTM live_price (price gap).
  6. For names without an explicit close, compare AXYS narrative
     direction vs MTM day-change sign (direction flip).
  7. Flag material divergences: |price gap| >= FLAG_PCT OR direction flip.
  8. Write portfolio/axys_closes_<today>.json and print a report.

Exit code: 0 = no material divergence, 1 = divergences flagged,
2 = no PDF / parse failure.

Source rule: the closing prices published are the NSE official close.
"AXYS Research" is the publisher/commentary, not a separate price source.
"""
from __future__ import annotations

import sys
import os
import re
import json
import subprocess
import datetime

PORTFOLIO_DIR = "/home/hermes/.trading/portfolio"
MTM_PATH = os.path.join(PORTFOLIO_DIR, "mtm_state.json")
CACHE_DIR = "/home/hermes/.hermes/cache/documents"
FLAG_PCT = 0.3  # material price-divergence threshold (%)

# Full AXYS company name (as printed in the per-share valuation tables)
# -> my trading symbol. The valuation tables list EVERY NSE name with
# clean columns:  Name  CurrentPrice  DailyChange(▲/▼ x%)  ...
FULL_NAME_TO_SYMBOL = {
    "Safaricom": "SCOM",
    "Equity Group Holdings": "EQTY",
    "KCB Group": "KCB",
    "ABSA Bank Kenya": "ABSA",
    "The Co-operative Bank of Kenya": "COOP",
    "East African Breweries": "EABL",
    "Kenya Power & Lighting Co": "KPLC",
    "Kenya Re- Insurance Corporation": "KNRE",
    "Standard Chartered Bank Kenya": "SCBK",
    "Bamburi Cement": "BAMB",
    "Total Kenya": "TOTL",
    "TotalEnergies Marketing Kenya": "TOTL",
}

# Row matcher:  NAME  PRICE  (▲/▼ (x.x%) | -)  ...
_ROW = re.compile(
    r"^(?P<name>[A-Z][A-Za-z0-9 &.()/',-]+?)\s+"
    r"(?P<price>\d[\d,]*(?:\.\d+)?)\s+"
    r"(?P<chg>(?:▲|▼)\s*\(?[\d.]+%?\)?|-)\s")


INCOMING_DIR = "/home/hermes/.trading/incoming"


def _resolve_pdf(pdf: str) -> str:
    """Return a PDF path the *current* user can read.

    If the given path is unreadable by the current user (e.g. it lives
    under another user's private dir like ~/.hermes, mode 700), fall back
    to a same-basename copy in INCOMING_DIR (which the trading pipeline
    controls). If nothing readable is found, returns the original path so
    the caller can emit a clear permission error instead of a misleading
    "PDF extracted empty".
    """
    if pdf and os.access(pdf, os.R_OK):
        return pdf
    cand = os.path.join(INCOMING_DIR, os.path.basename(pdf)) if pdf else ""
    if cand and os.access(cand, os.R_OK):
        return cand
    return pdf


def extract_text(pdf: str) -> tuple[str, str]:
    """Run ``pdftotext -layout``. Returns (stdout, stderr).

    Captures stderr so callers can surface the *real* failure reason
    (e.g. permission denied) instead of misreporting an empty PDF.
    """
    r = subprocess.run(["pdftotext", "-layout", pdf, "-"],
                       capture_output=True, text=True)
    return r.stdout, r.stderr


def parse_full_table(text: str) -> dict:
    """Parse the per-share valuation tables.

    Returns {symbol: {"close": float, "direction": +1/-1/0, "source": str}}.
    The valuation tables (sector blocks starting at 'BANKING',
    'COMMERCIAL AND SERVICES', etc., through end of document) list EVERY
    NSE name with clean columns:  Name  CurrentPrice  DailyChange(▲/▼ x%)  ...
    We restrict to that block so the Top Traded / index tables at the top
    (where column 2 is turnover, not a close) are ignored.
    """
    # Slice to the valuation block: from first sector header to end of doc.
    m = re.search(r"\n(BANKING|COMMERCIAL AND SERVICES|ENERGY & PETROLEUM|"
                  r"INVESTMENT|MANUFACTURING & ALLIED|TELECOMMUNICATION|"
                  r"AUTO & ALLIED)\b", text)
    if m:
        text = text[m.start():]
    res = {}
    for line in text.splitlines():
        mm = _ROW.match(line.strip())
        if not mm:
            continue
        name = mm.group("name").strip()
        sym = None
        for full, s in FULL_NAME_TO_SYMBOL.items():
            if name.startswith(full):
                sym = s
                break
        if not sym:
            continue
        price = float(mm.group("price").replace(",", ""))
        chg = mm.group("chg")
        direction = 1 if chg.startswith("▲") else (-1 if chg.startswith("▼") else 0)
        if sym not in res:  # first (most relevant sector) table wins
            res[sym] = {"close": price, "direction": direction,
                        "source": "NSE (AXYS Market Pulse)"}
    return res


def _recompute(mtm: dict) -> None:
    """Recompute per-position current_value/pnl/pnl_pct and summary totals
    after any live_price change. Mutates mtm in place."""
    tc = 0.0
    tv = 0.0
    for p in mtm["positions"]:
        lp = float(p["live_price"])
        p["current_value"] = round(p["shares"] * lp, 2)
        pnl = p["current_value"] - p["total_cost"]
        p["pnl"] = round(pnl, 2)
        p["pnl_pct"] = round(pnl / p["total_cost"] * 100, 2) if p["total_cost"] else 0.0
        tc += p["total_cost"]
        tv += p["current_value"]
    cash = float(mtm.get("cash", 0))
    mtm["summary"] = {
        "total_cost_basis": round(tc, 2),
        "total_market_value": round(tv, 2),
        "total_portfolio_value": round(tv + cash, 2),
        "total_pnl": round(tv - tc, 2),
        "total_pnl_pct": round((tv - tc) / tc * 100, 2) if tc else 0.0,
        "num_positions": len(mtm["positions"]),
    }


def apply_override(axys: dict, price_flags: set) -> int:
    """For symbols in price_flags (gap >= FLAG_PCT), overwrite MTM live_price
    with the AXYS official close, recompute derived fields, persist.
    Flips are intentionally NOT applied (monitor-only per directive).
    Returns number of positions corrected.
    """
    mtm = json.load(open(MTM_PATH))
    by_sym = {p["symbol"]: p for p in mtm["positions"]}
    corrected = 0
    for sym in price_flags:
        if sym in by_sym and sym in axys:
            by_sym[sym]["live_price"] = axys[sym]
            corrected += 1
    if corrected:
        _recompute(mtm)
        mtm["generated_at"] = datetime.datetime.now().isoformat()
        json.dump(mtm, open(MTM_PATH, "w"), indent=2)
    return corrected


def reapply_from_json(date_str: str) -> int:
    """Sticky re-apply: after the 15:30 market-close-refresh overwrites MTM
    with pipeline prices, re-impose yesterday's AXYS overrides so corrections
    survive until the feed is fixed at source.
    Reads portfolio/axys_closes_<date>.json -> applies price flags.
    """
    path = os.path.join(PORTFOLIO_DIR, f"axys_closes_{date_str}.json")
    if not os.path.exists(path):
        return 0
    data = json.load(open(path))
    axys = data.get("axys", {})
    price_flags = set()
    for row in data.get("rows", []):
        if "PRICE" in (row.get("flag") or ""):
            price_flags.add(row["symbol"])
    return apply_override(axys, price_flags)


def _normalize_report_date(raw: str) -> str | None:
    """Convert '31st July 2026' style AXYS date to ISO '2026-07-31'.

    Returns None if it cannot be parsed (caller falls back to today).
    """
    if not raw:
        return None
    cleaned = re.sub(r"(\d)(st|nd|rd|th)", r"\1", raw.strip())
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _is_stale(report_iso: str | None, max_days: int = 3) -> bool:
    """True if the PDF's report date is too old (or future) to apply to today.

    AXYS posts the Daily Market Watch the MORNING AFTER the session it covers,
    so a 1-day gap (yesterday's close, received today) is the NORMAL case and
    must NOT be treated as stale. We block only PDFs older than `max_days`
    (default 3, which tolerates a Friday -> Monday weekend gap) or dated in the
    future. Unparseable dates fall back to today (not stale).
    """
    if not report_iso:
        return False
    try:
        rep_d = datetime.date.fromisoformat(report_iso)
    except ValueError:
        return False
    delta = (datetime.date.today() - rep_d).days
    return delta > max_days or delta < 0


def main() -> int:
    args = sys.argv[1:]
    apply_mode = "--apply" in args
    reapply_mode = "--reapply" in args
    args = [a for a in args if a not in ("--apply", "--reapply")]
    if reapply_mode:
        # args[0] is the date string when provided
        date_str = args[0] if args else datetime.date.today().isoformat()
        n = reapply_from_json(date_str)
        print(f"\U0001f504 AXYS sticky re-apply for {date_str}: {n} position(s) corrected.")
        return 0
    pdf = args[0] if args else None
    if not pdf:
        files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)
                 if ("Market_Watch" in f or "AXYS" in f)]
        if not files:
            print("No AXYS PDF found in cache.")
            return 2
        pdf = max(files, key=os.path.getmtime)

    os.makedirs(INCOMING_DIR, exist_ok=True)
    pdf = _resolve_pdf(pdf)
    text, err = extract_text(pdf)
    if not text.strip():
        if not os.access(pdf, os.R_OK):
            who = os.getlogin() if hasattr(os, "getlogin") else "current"
            print(f"\u274c Cannot read PDF: {pdf}\n"
                  f"   Permission denied — user '{who}' cannot access it.\n"
                  f"   Fix: run as the file owner, or place a copy under "
                  f"{INCOMING_DIR}/.")
        else:
            print("PDF extracted empty.")
            if err.strip():
                print(f"   pdftotext stderr: {err.strip()}")
        return 2

    dm = re.search(r"(\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+20\d{2})", text)
    report_date = dm.group(1) if dm else datetime.date.today().isoformat()
    report_iso = _normalize_report_date(report_date)
    today_iso = datetime.date.today().isoformat()
    # Date-safety guard: AXYS publishes the Daily Market Watch the MORNING AFTER
    # the session it covers, so a PDF dated "yesterday" (received today) is the
    # NORMAL case and MUST be allowed. Only genuinely old PDFs (forwarded days
    # later, e.g. a Friday PDF surfacing the following Wednesday) are refused,
    # because applying a multi-session-stale close would corrupt today's book.
    # --force overrides. max_days=3 tolerates a Friday -> Monday weekend gap.
    stale = _is_stale(report_iso, max_days=3)
    forced = "--force" in args
    if stale and not forced:
        print(f"\u26d4 STALE PDF GUARD: PDF date '{report_date}' ({report_iso}) is "
              f"more than 3 days old vs today ({today_iso}).\n"
              f"   Refusing to apply this PDF's prices to today's MTM.\n"
              f"   Run with --force to override (not recommended for old PDFs).")
    out_date = report_iso if (stale and not forced) else today_iso

    explicit = parse_full_table(text)
    narrative = {}  # retained for completeness; full table supersedes it

    mtm = json.load(open(MTM_PATH))
    mtm_pos = {p["symbol"]: p for p in mtm["positions"]}

    rows, flags, price_flags = [], [], set()
    for sym, p in mtm_pos.items():
        lp = float(p["live_price"])
        chg = float(p.get("change_pct") or 0)
        rec = {"symbol": sym, "mtm_price": lp, "mtm_chg_pct": chg}
        if sym in explicit:
            ax = explicit[sym]
            ax_price = ax["close"]
            ax_dir = ax["direction"]
            rec["axys_price"] = ax_price
            rec["axys_direction"] = "up" if ax_dir > 0 else ("down" if ax_dir < 0 else "flat")
            rec["source"] = ax["source"]
            # 1) price gap vs AXYS official close
            delta = (lp - ax_price) / ax_price * 100 if ax_price else 0
            rec["price_delta_pct"] = round(delta, 3)
            if abs(delta) >= FLAG_PCT:
                rec["flag"] = f"PRICE {abs(delta):.2f}% off AXYS ({lp} vs {ax_price})"
                flags.append(rec["flag"])
                price_flags.add(sym)
            # 2) direction flip vs AXYS day-direction. Only flag when BOTH
            #    sides made a meaningful opposing move (avoids rounding dust:
            #    e.g. AXYS -0.2% vs MTM +0.00%, or AXYS flat vs MTM +0.29%).
            mtm_dir = 1 if chg > 0 else (-1 if chg < 0 else 0)
            if (mtm_dir != 0 and ax_dir != 0 and mtm_dir != ax_dir
                    and abs(chg) >= 0.1):
                flip = f"DIRECTION flip (AXYS {rec['axys_direction']}, MTM {chg:+.2f}%)"
                rec.setdefault("flag", flip)
                if flip not in flags:
                    flags.append(flip)
            rows.append(rec)
        else:
            rec["source"] = "not in AXYS Market Pulse today"
            rows.append(rec)

    # Apply corrective override to MTM for price-flagged names (flips monitor-only)
    # NEVER apply when the PDF is stale and not explicitly forced.
    if apply_mode and stale and not forced:
        applied = 0  # stale guard: no MTM write
    else:
        applied = apply_override({k: v["close"] for k, v in explicit.items()},
                                 price_flags) if apply_mode else 0

    out_path = os.path.join(PORTFOLIO_DIR,
                            f"axys_closes_{out_date}.json")
    json.dump({"date": report_date, "pdf": os.path.basename(pdf),
               "axys": {k: v["close"] for k, v in explicit.items()},
               "narrative_direction": narrative, "rows": rows,
               "applied_override": applied},
              open(out_path, "w"), indent=2)

    print(f"\U0001F4D1 AXYS vs MTM reconciliation \u2014 {report_date}")
    print(f"PDF: {os.path.basename(pdf)}\n")
    print(f"{'sym':6}{'MTM':>9}{'AXYS':>10}{'src_dir':>8}{'source':>24}  note")
    print("-" * 78)
    for r in rows:
        ax = r.get("axys_price", r.get("axys_direction", "-"))
        sdir = r.get("axys_direction", "-") if isinstance(ax, str) else ""
        print(f"{r['symbol']:6}{r['mtm_price']:>9}{str(ax):>10}{sdir:>8}{r['source'][:23]:>24}  {r.get('flag','')}")
    print()
    if flags:
        print(f"\u26a0\ufe0f  {len(flags)} divergence(s) flagged:")
        for f in flags:
            print(f"  - {f}")
    else:
        print("\u2705 All reconciled names tie out (no material divergence).")
    if apply_mode:
        if stale and not forced:
            print(f"\U0001f4be Saved historical reference (NO MTM change): "
                  f"{os.path.basename(out_path)}")
        else:
            print(f"\U0001f504 Override applied to MTM: {applied} position(s) corrected to AXYS official close.")
    elif stale and not forced:
        print(f"\U0001f4be Saved historical reference (no --apply, NO MTM change): "
              f"{os.path.basename(out_path)}")
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
