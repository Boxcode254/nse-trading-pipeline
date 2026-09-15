#!/usr/bin/env python3
"""Daily paper-trading supervision report -> Telegram.

Reads live auto-trader state and posts a FULL status report.

TP-005 (2026-09-15) changes:
  * portfolio numbers come from the ONE canonical read model
    (:mod:`trading.services.portfolio_status`) — no local re-derivation;
  * the Stage-1 gate section comes from
    :mod:`trading.services.benchmark_compare`, which returns
    ``comparison_status="incomparable"`` when the portfolio and the benchmark
    cannot be valued as of the same date over the same universe. When it is
    incomparable the report says so and publishes NO gap;
  * benchmark/portfolio staleness is COMPUTED from the stamps, never written
    as a fixed string;
  * hardcoded historical claims (fix dates, "last run today") are gone.

TP-008 (2026-09-15) changes:
  * the integrity section reads the CURRENT status record
    (``portfolio/integrity_status.json``, maintained by
    ``scripts/book_integrity_check.py``) instead of the tail of the
    append-only incident log;
  * it distinguishes current status (with its own computed age) from the
    "last recorded failure" (also with a computed age) and prints no
    historical failure as if it were current.

``build_report()`` returns the message text without side effects so the logic
can be exercised read-only in tests/smokes; ``main()`` persists the copy and
sends it.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load Telegram creds from ~/.env (same as trading.dashboard)
try:
    from dotenv import load_dotenv

    load_dotenv(Path.home() / ".env")
except Exception:  # noqa: BLE001
    pass

from trading.services import benchmark_compare as bc  # noqa: E402
from trading.services import integrity_status as istat  # noqa: E402
from trading.services import portfolio_status as ps  # noqa: E402

BASE = Path.home() / ".trading"
PF = BASE / "portfolio"
LOG = BASE / "logs"
INIT_CAPITAL = 100000.0


def load(p):
    with open(p) as f:
        return json.load(f)


def integrity_lines(pf_dir, now=None):
    """Current book-integrity status (TP-008).

    Reads the CURRENT status record (``portfolio/integrity_status.json``,
    written by every scheduled ``book_integrity_check.py`` run) — never the
    tail of the append-only incident log, which is history. Ages are computed
    from the record's stamps; no date or staleness string is hardcoded here.
    """
    return istat.format_report_lines(istat.load(pf_dir), now=now)


def _grab_section(txt, header):
    out = []
    capture = False
    for line in txt.splitlines():
        if header in line:
            capture = True
            continue
        if capture:
            if line.strip().startswith(("1.", "2.", "3.", "4.", "5.")):
                out.append(line.strip())
            elif line.strip() == "" or out:
                if len(out) >= 3 or (out and line.strip().startswith("💼")):
                    break
    return out[:3]


def build_report() -> tuple[str, dict]:
    """Build the daily supervision message. Read-only; returns (text, meta)."""
    # ---- Portfolio — the canonical read model (single source of numbers) ----
    status = ps.current_status(str(PF))
    total_value = status.get("portfolio_value")
    cash = status.get("cash") or 0.0
    invested = status.get("invested") or 0.0
    positions = status.get("positions") or []
    cost_basis = sum(float(p.get("total_cost") or 0.0) for p in positions)
    pnl = status.get("total_pnl")
    pnl_pct = status.get("total_pnl_pct")
    gen_at = status.get("as_of") or "unknown"
    pf_age = status.get("age_days")
    price_date = status.get("price_date")
    provenance = status.get("resolver") or {}
    pos_sorted = sorted(positions, key=lambda p: p.get("current_value") or 0, reverse=True)

    # ---- Benchmark comparison — same date, same universe, or nothing ----
    comparison = bc.compare(dir_path=str(PF), portfolio_status=status)

    # ---- Signals ----
    sig_rows = []
    sig_path = BASE / "signals.csv"
    if sig_path.exists():
        with open(sig_path, newline="") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if row:
                    sig_rows.append(row)
    sig_last = sig_rows[-1][0] if sig_rows else "none"
    sig_dates = Counter(x[0][:10] for x in sig_rows)
    sig_recent = dict(sorted(sig_dates.items())[-4:])

    # ---- Integrity (current status record, NOT the incident-log tail) ----
    integrity = integrity_lines(PF)

    # ---- Morning brief (latest) ----
    briefs = sorted(glob.glob(str(LOG / "morning-*.md")))
    brief_txt = ""
    brief_when = "no morning brief on file"
    if briefs:
        brief_path = Path(briefs[-1])
        brief_txt = brief_path.read_text()
        mtime = datetime.fromtimestamp(brief_path.stat().st_mtime, tz=timezone.utc)
        brief_when = "latest %s" % mtime.date().isoformat()

    mkt_score = ""
    for line in brief_txt.splitlines():
        if "Opportunity score" in line:
            mkt_score = line.strip()
            break
    top_ops = _grab_section(brief_txt, "TOP OPPORTUNITIES")
    avoid = _grab_section(brief_txt, "ASSETS TO AVOID")

    # ---- Fundamental audit ----
    fa_path = BASE / "audit" / "fundamental_audit.json"
    fa = load(fa_path) if fa_path.exists() else {}
    recs = fa.get("records", [])
    health = Counter(r.get("health_at_exec", "UNKNOWN") for r in recs)
    stale_n = sum(1 for r in recs if r.get("stale"))
    fa_date = recs[0].get("audit_id", "")[:10] if recs else "n/a"

    # ---- Compose ----
    L = []
    L.append(
        "📊 *DAILY PAPER-TRADING SUPERVISION — %s*"
        % datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    L.append("")
    L.append(
        "_Note: legacy `trading.dashboard` is archived (2026-07-17) and emits only a stub. "
        "This report reads the canonical portfolio read model "
        "(trading.services.portfolio_status) and the same-date benchmark comparison "
        "(trading.services.benchmark_compare)._"
    )
    L.append("")
    if total_value is None:
        L.append("*PORTFOLIO: NO CURRENT STATE* — %s" % status.get("source"))
    else:
        L.append("*PORTFOLIO (canonical read model — %s)*" % status.get("source"))
        L.append(
            "• Total value: *KES %s*   (as of %s%s)"
            % (
                f"{total_value:,.2f}",
                gen_at,
                "  ·  %.2fd old" % pf_age if isinstance(pf_age, (int, float)) else "",
            )
        )
        L.append("• Cash: KES %s  (%.1f%% of book)" % (f"{cash:,.2f}", cash / total_value * 100))
        L.append("• Invested (market): KES %s" % f"{invested:,.2f}")
        L.append("• Cost basis: KES %s" % f"{cost_basis:,.2f}")
        if pnl is not None:
            L.append(
                "• Unrealized P&L: *%sKES %s (%+.2f%%)*"
                % ("+" if pnl >= 0 else "-", f"{abs(pnl):,.2f}", pnl_pct or 0.0)
            )
        L.append("• Positions: %d" % len(positions))
        L.append(
            "• Valuation basis: %s  ·  prices %s"
            % (status.get("valuation_basis"), price_date or "unknown")
        )
        if provenance.get("official_close_file"):
            L.append(
                "• Price provenance: %s (age %sd, stale=%s)  %s"
                % (
                    provenance.get("official_close_file"),
                    provenance.get("official_close_age_days"),
                    provenance.get("stale"),
                    provenance.get("sources"),
                )
            )
    L.append("")
    if pos_sorted:
        L.append("*POSITIONS (live MTM, by market value)*")
        rows = ["%-6s %5s %8s %8s %10s %7s %6s" % ("SYM", "SH", "AVG", "PRICE", "MKTVAL", "PNL%", "SRC")]
        for p in pos_sorted:
            rows.append(
                "%-6s %5d %8.2f %8.2f %10.2f %6s %6s"
                % (
                    p.get("symbol"),
                    p.get("shares") or 0,
                    p.get("avg_cost") or 0.0,
                    p.get("live_price") or 0.0,
                    p.get("current_value") or 0.0,
                    ("%+.2f%%" % p["pnl_pct"]) if isinstance(p.get("pnl_pct"), (int, float)) else "n/a",
                    p.get("price_source") or "?",
                )
            )
        L.append("```")
        L.append("\n".join(rows))
        L.append("```")
        L.append("")

    L.append("*STAGE-1 GATE STANDING (same-date, same-universe comparison)*")
    if comparison.get("comparison_status") == "comparable":
        pf_ret = comparison["portfolio"]["return_pct"]
        bm_ret = comparison["benchmark"]["return_pct"]
        L.append(
            "• Strategy: %+.2f%%  (KES %s vs KES %s init)"
            % (pf_ret, f"{comparison['portfolio']['value']:,.2f}", f"{comparison['initial_capital']:,.2f}")
        )
        L.append(
            "• Benchmark (eq-wt hold, %s): %+.2f%%  (KES %s @ %s)"
            % (
                comparison.get("benchmark_universe_version"),
                bm_ret,
                f"{comparison['benchmark']['value']:,.2f}",
                comparison.get("benchmark_snapshot_date"),
            )
        )
        L.append(
            "• As of %s (prices %s) · universe %d · same resolver, same date"
            % (
                comparison.get("as_of"),
                comparison.get("price_date"),
                comparison.get("eligible_universe_size"),
            )
        )
        L.append(
            "• Gap (gross): %+.2f%%   →   net of declared costs: %+.2f%%"
            % (comparison["gap_gross_pct"], comparison["gap_net_pct"])
        )
        L.append(
            "• Verdict: %s  ·  deadline %s"
            % (
                "✅ ON TRACK" if comparison.get("gate_ok") else "⚠ BEHIND GATE",
                comparison.get("gate_deadline"),
            )
        )
    else:
        L.append("• " + bc.format_comparison(comparison)[0])
        for r in comparison.get("reasons") or []:
            L.append("  · %s" % r.get("detail"))
        bench = comparison.get("benchmark") or {}
        if bench.get("recorded_value") is not None:
            L.append(
                "  · benchmark record: KES %s @ %s (universe version %s)"
                % (
                    f"{bench['recorded_value']:,.2f}",
                    bench.get("recorded_date"),
                    comparison.get("benchmark_universe_version"),
                )
            )
        if bench.get("recomputed_value") is not None:
            L.append(
                "  · same-date recompute for reference only (NOT used for any verdict): KES %s"
                % f"{bench['recomputed_value']:,.2f}"
            )
        L.append("• Verdict: ⛔ BLOCKED — no gap published from a mismatched date/universe")
        L.append("• Deadline unchanged: %s" % comparison.get("gate_deadline"))
    L.append("")
    L.append("*SIGNAL ENGINE*")
    L.append("• Alive: last signal %s  ·  %d signals total" % (sig_last, len(sig_rows)))
    L.append("• Recent daily counts: " + ", ".join("%s=%d" % (d, c) for d, c in sig_recent.items()))
    L.append("")
    L.append("*MORNING BRIEF (%s)*" % brief_when)
    if mkt_score:
        L.append("• " + mkt_score)
    if top_ops:
        L.append("• Top: " + " | ".join(top_ops))
    if avoid:
        L.append("• Avoid: " + " | ".join(avoid))
    L.append("")
    L.append("*INTEGRITY / AUDIT*")
    for ln in integrity:
        L.append("• " + ln)
    if recs:
        L.append(
            "• Fundamental audit (%s): %d fills · HEALTHY=%d WATCH=%d UNHEALTHY=%d · stale=%d"
            % (
                fa_date,
                len(recs),
                health.get("HEALTHY", 0),
                health.get("WATCH", 0),
                health.get("UNHEALTHY", 0),
                stale_n,
            )
        )
    else:
        L.append("• No fundamental audit records on file.")
    L.append("")
    L.append("*AUTONOMY*")
    L.append("• Signal pipeline + morning brief automated daily (%s)." % brief_when)
    L.append("• Auto-trader executes paper trades 10:30 EAT. 100% PAPER — no real capital at risk.")
    L.append("")
    L.append(
        "_Sources: portfolio/mtm_state.json (via trading.services.portfolio_status), "
        "portfolio/benchmark.json (via trading.services.benchmark_compare), signals.csv, "
        "portfolio/integrity_status.json, audit/fundamental_audit.json, logs/morning-*.md._"
    )

    integrity_record = istat.load(PF)
    meta = {
        "portfolio_value": total_value,
        "portfolio_as_of": gen_at,
        "portfolio_age_days": pf_age,
        "integrity_status": (integrity_record or {}).get("status"),
        "integrity_checked_at": (integrity_record or {}).get("checked_at"),
        "integrity_age_days": istat.age_days((integrity_record or {}).get("checked_at")),
        "last_recorded_failure": (integrity_record or {}).get("last_failure"),
        "comparison_status": comparison.get("comparison_status"),
        "comparison_reason_codes": comparison.get("reason_codes"),
        "benchmark_age_days": comparison.get("benchmark_age_days"),
        "gap_gross_pct": comparison.get("gap_gross_pct"),
        "gap_net_pct": comparison.get("gap_net_pct"),
        "gate_ok": comparison.get("gate_ok"),
    }
    return "\n".join(L), meta


def main() -> int:
    msg, meta = build_report()

    # ---- Persist copy ----
    LOG.mkdir(parents=True, exist_ok=True)
    out_path = LOG / ("daily-supervision-%s.md" % datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    out_path.write_text(msg)
    print("Report written to", out_path)
    print("Comparison:", meta.get("comparison_status"), meta.get("comparison_reason_codes"))

    # ---- Send to Telegram ----
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_HOME_CHANNEL") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram not configured; aborting send.")
        raise SystemExit(1)
    # Telegram legacy "Markdown" parser hard-fails the entire message on any
    # unparseable entity (e.g. italic with embedded backticks -> HTTP 400).
    # For an unattended cron, reliable delivery > formatting: send as plain text.
    msg_plain = msg.replace("`", "").replace("*", "").replace("_", "")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat, "text": msg_plain,
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
            body = resp.read().decode()
        print("Telegram send:", "OK" if ok else "FAILED", resp.status if 'resp' in dir() else "")
        if not ok:
            print(body[:500])
    except Exception as e:  # noqa: BLE001
        print("Telegram send error:", e)
        raise SystemExit(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
