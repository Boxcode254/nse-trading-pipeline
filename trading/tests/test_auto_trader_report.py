"""End-of-day report rendering regressions (task t_a9e209de).

Two defects from the KNRE incident window:

1. Report-label audit: the 2026-10-16 KNRE stop-loss SELL (1802 sh @ 4.0639,
   reason "Stop-loss triggered: KNRE is -8.3% below avg cost of KES 4.44")
   was reported in the task as having rendered under "🟢 Bought". The archived
   cron output (~/.hermes/cron/output/bd8361975bd7/2026-10-16_10-31-28.md)
   shows it rendered correctly under "🔴 Sold" — no archived report (all 35
   scanned) ever put a sell under "Bought". These tests LOCK the correct
   behaviour in with the exact 10-16 trade shape so the label bug, if it ever
   had a path, can never regress: sells must route via add_sell -> stocks_sold
   and build() must render them under "Sold", never "Bought".

2. Empty skip reason: validate_plan_constraints emits {level, kind, detail}
   but the renderer read {severity, type, message}, producing
   "**None**: [WARN] CONSTRAINT: " (seen in the 2026-10-19 10:31 report).
   _format_plan_violation must fall back across both key spellings and never
   return a reason that ends in a bare colon.
"""

from __future__ import annotations

import re

from trading.auto_trader import AutoTraderReport, _format_plan_violation

# The exact 2026-10-16 10:31 KNRE stop-loss trade shape (from the archived
# report + portfolio/transactions.json). add_sell(symbol, shares, price, value, reason)
KNRE_STOP_SELL = ("KNRE", 1802, 4.0639, 1802 * 4.0639,
                  "Stop-loss triggered: KNRE is -8.3% below avg cost of KES 4.44")


def _bought_section(report_text: str) -> str | None:
    m = re.search(r"━+ 🟢 Bought ━+\n(.*?)(?=\n\n|\Z)", report_text, re.S)
    return m.group(1) if m else None


def _sold_section(report_text: str) -> str | None:
    m = re.search(r"━+ 🔴 Sold ━+\n(.*?)(?=\n\n|\Z)", report_text, re.S)
    return m.group(1) if m else None


# ── 1. Sells never render under "Bought" ────────────────────────────────────


def test_stop_loss_sell_renders_under_sold_not_bought():
    """10-16 KNRE stop-loss shape: must appear under Sold, never Bought."""
    r = AutoTraderReport()
    r.add_sell(*KNRE_STOP_SELL)
    text = r.build()

    bought = _bought_section(text)
    sold = _sold_section(text)

    assert bought is None, "no buys this session — Bought section must not exist"
    assert sold is not None
    assert "KNRE" in sold
    assert "1802" in sold
    assert "Stop-loss triggered" in sold


def test_buy_and_sell_render_in_their_own_sections():
    """Mixed session (10-19 re-buy + a stop-loss sell): each under its label."""
    r = AutoTraderReport()
    r.add_sell(*KNRE_STOP_SELL)
    r.add_buy(
        symbol="SCOM",
        shares=100,
        price=35.54,
        value=3554.0,
        reason="Sector insurance is -7.6% under target (risk weight 50%)",
    )
    text = r.build()

    bought = _bought_section(text) or ""
    sold = _sold_section(text) or ""
    assert "SCOM" in bought and "KNRE" not in bought
    assert "KNRE" in sold and "SCOM" not in sold
    # KNRE appears with its SELL share count in Sold, never its BUY count.
    assert re.search(r"KNRE\*?\*?: 1802 shares", sold)
    assert "1802" not in bought


def test_add_sell_never_touches_stocks_bought():
    """Structural guard: add_sell is the ONLY route for sells into the report."""
    r = AutoTraderReport()
    r.add_sell(*KNRE_STOP_SELL)
    assert r.stocks_bought == []
    assert len(r.stocks_sold) == 1
    assert r.stocks_sold[0]["symbol"] == "KNRE"


# ── 2. Violation rendering never emits an empty message ─────────────────────


def test_warn_cap_violation_uses_kind_and_detail():
    """Producer spelling {level, kind, detail} (validate_plan_constraints)."""
    sym, rendered = _format_plan_violation({
        "level": "warn",
        "kind": "WARN_CAP",
        "sector": "insurance",
        "symbol": None,
        "current_pct": 7.6,
        "limit_pct": 15.0,
        "detail": "insurance at 7.6% exceeds WARN cap 15% — review concentration",
    })
    assert sym == "PORTFOLIO"  # None symbol -> PORTFOLIO, never "None"
    assert rendered == (
        "[WARN] WARN_CAP: insurance at 7.6% exceeds WARN cap 15% — "
        "review concentration"
    )


def test_cooldown_skip_violation_uses_severity_type_message():
    """Cooldown-skip spelling {severity, type, message} still renders."""
    sym, rendered = _format_plan_violation({
        "severity": "SKIP",
        "symbol": "KNRE",
        "type": "STOP_COOLDOWN",
        "message": "Stop-loss re-entry cooldown: exited 2026-10-16 @ KES 4.0639",
    })
    assert sym == "KNRE"
    assert rendered == (
        "[SKIP] STOP_COOLDOWN: Stop-loss re-entry cooldown: "
        "exited 2026-10-16 @ KES 4.0639"
    )


def test_violation_with_no_message_falls_back_to_kind_and_stats():
    """The 10-19 bug: {level, kind} only -> must not render 'CONSTRAINT: '."""
    sym, rendered = _format_plan_violation({
        "level": "warn",
        "kind": "WARN_CAP",
        "sector": "insurance",
        "symbol": None,
        "current_pct": 7.6,
        "limit_pct": 15.0,
    })
    assert rendered.startswith("[WARN] WARN_CAP: ")
    assert "insurance" in rendered
    assert "15.0%" in rendered
    assert not rendered.rstrip().endswith(":")


def test_rendered_violation_never_ends_with_bare_colon():
    """Fuzz-ish guard across degenerate violation dicts."""
    degenerate = [
        {},
        {"message": "   "},
        {"kind": "HARD_CAP"},
        {"type": "STOP_COOLDOWN", "message": ""},
        {"level": "error", "detail": ""},
    ]
    for v in degenerate:
        _, rendered = _format_plan_violation(v)
        assert rendered.rstrip().endswith(":") is False, (
            f"empty reason for {v!r}: {rendered!r}"
        )
        # And it always renders as "[SEV] TYPE: <non-empty>"
        assert re.match(r"^\[[A-Z]+\] [A-Z_]+: \S", rendered), rendered
