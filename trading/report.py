"""
Reporting & Interpretation Engine — Phase 1.

Transforms raw technical analysis into a layered, decision-friendly daily
market briefing.  The guiding principle is:

    Explain, don't just display.
    Prioritise interpretation over raw numbers.
    Make "no signal" a valid and valuable outcome.

14 sections are composed, each as a separate private function, then assembled
from the top down so the most actionable information appears first.  The
final report is bounded to ~3800 characters so it fits a single Telegram
message (4096 limit) with headroom.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import json

import pandas as pd

from . import config
from .signals.validator import calculate_confidence

# ── Constants ─────────────────────────────────────────────────────────

EMOJI_SIGNAL = {"BUY": "\U0001f7e2", "SELL": "\U0001f534",
                "WATCH": "\U0001f7e1", "HOLD": "\u26aa", "NO_DATA": "\u274c"}
EMOJI_MOOD = {"bullish": "\U0001f7e2", "neutral": "\U0001f7e1", "bearish": "\U0001f534"}
EMOJI_ARROW = {"bullish": "\u2191", "bearish": "\u2193", "flat": "\u2192"}
SEP = "\u2501" * 35
MAX_CHARS = 3800


def _fmt(x: Any, d: int = 5) -> str:
    """Format a number or return '—' for NaN/None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "\u2014"
    if math.isnan(v):
        return "\u2014"
    return f"{v:.{d}f}"


def _int_pct(x: Any) -> str:
    """Round a number to integer with % sign."""
    try:
        return f"{int(round(float(x)))}%"
    except (TypeError, ValueError, ValueError):
        return "\u2014"


# ── Helpers ────────────────────────────────────────────────────────────


def _trend_dir(sig: dict | None) -> str:
    """Return 'bullish', 'bearish', or 'flat' based on SMA relationship."""
    if sig is None:
        return "flat"
    f = sig.get("sma_fast")
    s = sig.get("sma_slow")
    if f is None or s is None or math.isnan(float(f)) or math.isnan(float(s)):
        return "flat"
    diff = abs(float(f) - float(s)) / float(s)
    if float(f) > float(s) and diff > 0.002:
        return "bullish"
    if float(f) < float(s) and diff > 0.002:
        return "bearish"
    return "flat"


def _market_mood(pair_signals: dict[str, dict]) -> str:
    """Overall market mood from all pairs."""
    dirs = [_trend_dir(s) for s in pair_signals.values()]
    bulls = sum(1 for d in dirs if d == "bullish")
    bears = sum(1 for d in dirs if d == "bearish")
    if bulls > bears:
        return "bullish"
    if bears > bulls:
        return "bearish"
    return "neutral"


def _confidence_level(conf: float) -> str:
    if conf >= 70:
        return "High"
    if conf >= 40:
        return "Medium"
    return "Low"


def _conf_bar(conf: float, width: int = 10) -> str:
    """Visual confidence bar like ██████░░░░."""
    filled = max(0, min(width, int(round(conf / 100 * width))))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _visual_signal(signal: str | None) -> str:
    return EMOJI_SIGNAL.get(signal or "", "\u26aa")


def _rsi_note(rsi_val: Any) -> str:
    try:
        r = float(rsi_val)
    except (TypeError, ValueError):
        return ""
    if r >= config.RSI_OVERBOUGHT:
        return "overbought"
    if r <= config.RSI_OVERSOLD:
        return "oversold"
    if r > 60:
        return "bullish momentum"
    if r < 40:
        return "bearish pressure"
    return "neutral"


def _sma_note(sig: dict) -> str:
    dir_ = _trend_dir(sig)
    if dir_ == "bullish":
        return "Above SMA20 · Above SMA50"
    if dir_ == "bearish":
        return "Below SMA20 · Below SMA50"
    return "Near SMA20 · Near SMA50"


# ── 1. Executive Summary ───────────────────────────────────────────────


def _executive_summary(pair_signals: dict[str, dict],
                       rejected: list[dict]) -> str:
    mood = _market_mood(pair_signals)
    mood_emoji = EMOJI_MOOD[mood]
    today = datetime.now(timezone.utc).strftime("%d %b %Y  %H:%M UTC")

    buys = sum(1 for s in pair_signals.values() if s.get("signal") == "BUY")
    sells = sum(1 for s in pair_signals.values() if s.get("signal") == "SELL")
    holds = sum(1 for s in pair_signals.values() if s.get("signal") == "HOLD")
    watches = sum(1 for s in pair_signals.values()
                  if s.get("signal") == "WATCH")

    highs = sum(1 for s in pair_signals.values()
                if s.get("confidence", 0) >= 70)
    mids = sum(1 for s in pair_signals.values()
               if 40 <= s.get("confidence", 0) < 70)
    lows = len(rejected)

    lines = [
        "\U0001f4ca **Daily Market Brief**",
        "",
        f"Date: {today}",
        "",
        f"**Overall Market Mood**",
        f"{mood_emoji}  {mood.capitalize()}",
        "",
        f"**Signals**",
        f"\U0001f7e2 BUY  {buys}    \U0001f534 SELL {sells}    "
        f"\U0001f7e1 WATCH {watches}    \u26aa HOLD {holds}",
        "",
        f"**Confidence**",
        f"\U0001f7e2 High   {highs}",
        f"\U0001f7e1 Medium {mids}",
        f"\u26d4 Low (Filtered) {lows}",
    ]

    # Recommendation
    if buys > 0 and highs > 0:
        lines.append("")
        lines.append("**Recommendation**")
        lines.append(f"\U0001f6a9  {buys} high-confidence BUY candidate(s) detected. "
                     "Review asset cards below.")
    elif sells > 0 and highs > 0:
        lines.append("")
        lines.append("**Recommendation**")
        lines.append(f"\U0001f6a9  {sells} high-confidence SELL candidate(s) detected. "
                     "Review asset cards below.")
    else:
        lines.append("")
        lines.append("**Recommendation**")
        lines.append("No high-confidence opportunities today.")
        lines.append("Remain patient and wait for confirmation.")

    return "\n".join(lines)


# ── 1b. Change Since Yesterday ────────────────────────────────────────


def _change_since_yesterday(pair_signals: dict[str, dict]) -> str:
    """Compare today's key metrics against yesterday's closing values.

    Reads the previous day's run log and computes deltas for the
    opportunity score, signal counts, and per-symbol RSI changes.
    Returns an empty string if no previous data is available.
    """
    import json
    from datetime import timedelta

    today = datetime.now(timezone.utc)
    yesterday_path = os.path.join(
        config.LOGS_DIR,
        f"{(today - timedelta(days=1)).strftime('%Y-%m-%d')}.json"
    )

    try:
        with open(yesterday_path) as f:
            yesterday_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""

    if not yesterday_data:
        return ""

    # Use the last run of yesterday (closing state)
    yesterday = yesterday_data[-1]
    y_sigs: list[dict] = yesterday.get("latest_signals", [])

    # Build a lookup: symbol → yesterday's signal
    y_by_sym: dict[str, dict] = {}
    for s in y_sigs:
        sym = s.get("symbol", "") or s.get("pair", "").replace("/", "")
        y_by_sym[sym] = s

    # ── Opportunity score delta ──
    # Rebuild yesterday's pair_signals-like dict for the score calculator
    y_pair_signals: dict[str, dict] = {}
    for s in yesterday.get("latest_signals", []):
        sym = s.get("symbol", "") or s.get("pair", "").replace("/", "")
        # Map back to the pair format used in config
        for p in config.PAIRS:
            if p.replace("/", "") == sym:
                y_pair_signals[p] = {
                    "signal": s.get("decision") or s.get("signal", "HOLD"),
                    "confidence": s.get("confidence", 0),
                }

    today_score = _calc_opportunity_score(pair_signals)
    yesterday_score = _calc_opportunity_score(y_pair_signals) if y_pair_signals else None

    # ── Signal count delta ──
    def _sig_counts(sig_list: list[dict]) -> dict:
        bu = se = 0
        for s in sig_list:
            d = (s.get("decision") or s.get("signal", "")).upper()
            if d == "BUY":
                bu += 1
            elif d == "SELL":
                se += 1
        return {"BUY": bu, "SELL": se}

    today_sigs_list = list(pair_signals.values())
    y_totals = _sig_counts(yesterday.get("latest_signals", []))
    t_totals = _sig_counts(today_sigs_list)
    new_buys = max(0, t_totals["BUY"] - y_totals["BUY"])
    new_sells = max(0, t_totals["SELL"] - y_totals["SELL"])

    # ── Market conviction ──
    conviction = ""
    if yesterday_score is not None:
        diff = today_score - yesterday_score
        if diff > 5:
            conviction = "Strengthening \u2197"
        elif diff < -5:
            conviction = "Weakening \u2198"
        else:
            conviction = "Stable \u2192"

    # ── Biggest change per symbol ──
    biggest: tuple[str, str] | None = None
    max_rsi_delta = 0.0
    for p in config.PAIRS:
        sym = p.replace("/", "")
        today_sig = pair_signals.get(p)
        y_sig = y_by_sym.get(sym)
        if today_sig is None or y_sig is None:
            continue
        t_rsi = today_sig.get("rsi")
        y_rsi = y_sig.get("rsi")
        if t_rsi is None or y_rsi is None:
            continue
        try:
            delta = abs(float(t_rsi) - float(y_rsi))
        except (TypeError, ValueError):
            continue
        if delta > max_rsi_delta:
            max_rsi_delta = delta
            y_val = _fmt(float(y_rsi), 0)
            t_val = _fmt(float(t_rsi), 0)
            arrow = "\u2191" if float(t_rsi) > float(y_rsi) else "\u2193"
            biggest = (sym, f"RSI: {y_val} \u2192 {t_val} {arrow} ({delta:.0f})")

    # ── Build section ──
    lines = [
        "\U0001f4c5 **Change Since Yesterday**",
        "",
    ]

    # Score comparison
    score_line = f"Market Opportunity Score\n  Yesterday: {yesterday_score if yesterday_score is not None else '—'}\n  Today:     {today_score}"
    if yesterday_score is not None:
        diff = today_score - yesterday_score
        arrow = "\u2197" if diff > 0 else "\u2198" if diff < 0 else "\u2192"
        score_line += f" {arrow} ({diff:+d})"
    lines.append(score_line)
    lines.append("")

    # Signal count changes
    lines.append(f"New BUY Signals: {new_buys}")
    lines.append(f"New SELL Signals: {new_sells}")
    lines.append("")

    # Conviction
    if conviction:
        lines.append(f"Market Conviction:\n  {conviction}")
        lines.append("")

    # Biggest change
    if biggest:
        sym, change = biggest
        lines.append(f"Biggest Change:\n  {sym} {change}")

    return "\n".join(lines)


# ── 1c. Top Opportunities (from ranking engine) ─────────────────────


def _top_opportunities(ranking: list[dict] | None, top_n: int = 3) -> str:
    """Render the "Top Opportunities" section from the ranking engine.

    ``ranking`` is a list of per-asset dicts as produced by
    :func:`ranking.ranker.rank_assets` — each with ``symbol``,
    ``score``, ``recommendation``, ``holding_period``, ``reason``.

    Returns an empty string if no ranking data is supplied, so the
    section can be safely included even when the ranker isn't wired
    in (e.g. legacy callers).
    """
    if not ranking:
        return ""

    from .ranking.output import format_top_opportunities
    return format_top_opportunities(ranking, top_n=top_n)


# ── 2. Market Opportunity Score ────────────────────────────────────────


def _calc_opportunity_score(pair_signals: dict[str, dict]) -> int:
    """Return a numeric market opportunity score 0–100 from signal data."""
    if not pair_signals:
        return 0
    sig_score = 0.0
    for s in pair_signals.values():
        conf = s.get("confidence", 0)
        if s.get("signal") in ("BUY", "SELL"):
            sig_score += conf * 1.5
        elif s.get("signal") == "WATCH":
            sig_score += conf * 0.7
        else:
            sig_score += conf * 0.3
    return min(100, int(sig_score / max(1, len(pair_signals))))


def _opportunity_score(pair_signals: dict[str, dict]) -> str:
    score = _calc_opportunity_score(pair_signals)

    label = ""
    activity = ""
    if score >= 70:
        label = "Strong trending environment."
        activity = "Review all BUY / SELL candidates."
    elif score >= 40:
        label = "Moderate opportunities available."
        activity = "Review WATCH signals for confirmation."
    elif score >= 20:
        label = "Quiet market. Few high-quality setups."
        activity = "Observe only."
    else:
        label = "Low-activity market. No clear direction."
        activity = "Patience recommended."

    return (
        f"**Market Opportunity Score**\n\n"
        f"**{score} / 100**\n\n"
        f"{label}\n"
        f"Recommended activity: {activity}"
    )


# ── 3. Market Heatmap ──────────────────────────────────────────────────


def _heatmap(pair_signals: dict[str, dict]) -> str:
    lines = ["**Market Heatmap**", ""]
    for pair in config.PAIRS:
        sig = pair_signals.get(pair)
        if sig is None:
            lines.append(f"{pair:<10s}  \u274c NO DATA")
            continue
        emoji = _visual_signal(sig.get("signal"))
        lines.append(f"{pair:<10s}  {emoji} {sig.get('signal', '?'):<5s}  "
                     f"conf: {_int_pct(sig.get('confidence', 0))}")
    return "\n".join(lines)


# ── 4. Individual Asset Cards ──────────────────────────────────────────


def _asset_card(pair: str, sig: dict | None, df: pd.DataFrame | None) -> str:
    if sig is None:
        return f"**{pair}**\n\n\U0001f534 No data available for this scan.\n"

    signal = sig.get("signal", "HOLD")
    emoji = _visual_signal(signal)
    direction = _trend_dir(sig)
    arrow = EMOJI_ARROW.get(direction, "\u2192")
    conf = float(sig.get("confidence", 0))

    lines = [
        f"**{pair}**\n",
        f"Status     {emoji}  {signal}",
        f"Trend      {arrow}  {direction.capitalize()}",
        f"Confidence {_int_pct(conf)}",
        f"Price      {_fmt(sig.get('price', ''))}",
    ]

    # Price vs SMA
    lines.append(f"Price      {_sma_note(sig)}")

    # RSI note
    rsi_note = _rsi_note(sig.get("rsi"))
    if rsi_note:
        lines.append(f"Momentum   RSI = {_fmt(sig.get('rsi'), 0)} ({rsi_note})")

    # Support / resistance from recent DataFrame
    if df is not None and not df.empty:
        recent = df.tail(20)
        support = recent["low"].min()
        resistance = recent["high"].max()
        lines.append(f"Support    {_fmt(support)}")
        lines.append(f"Resistance {_fmt(resistance)}")

    # Interpretation
    interp = _interpret(pair, sig, direction, rsi_note)
    lines.append(f"**Interpretation**\n{interp}")

    # Suggested action
    action = _suggest_action(signal, conf)
    lines.append(f"**Action**\n{action}")

    return "\n".join(lines)


def _interpret(pair: str, sig: dict, direction: str, rsi_note: str) -> str:
    """Plain-language interpretation of what the indicators mean."""
    parts = []
    signal = sig.get("signal", "HOLD")

    if direction == "bullish":
        parts.append("Bulls remain in control.")
    elif direction == "bearish":
        parts.append("Bears remain in control.")
    else:
        parts.append("Price is consolidating.")

    rsi = sig.get("rsi")
    if rsi is not None and not math.isnan(float(rsi)):
        r = float(rsi)
        if r <= config.RSI_OVERSOLD:
            parts.append("Market is approaching oversold territory.")
            if signal != "BUY":
                parts.append("No confirmed reversal yet.")
        elif r >= config.RSI_OVERBOUGHT:
            parts.append("Market is approaching overbought territory.")
            if signal != "SELL":
                parts.append("No confirmed reversal yet.")
        elif direction == "bullish" and r > 55:
            parts.append("Momentum aligns with the uptrend.")
        elif direction == "bearish" and r < 45:
            parts.append("Momentum aligns with the downtrend.")

    if signal == "BUY":
        parts.append("Bullish crossover confirmed with momentum support.")
    elif signal == "SELL":
        parts.append("Bearish crossover confirmed with momentum support.")
    elif signal == "WATCH":
        parts.append("One or more indicators approaching threshold.")
        parts.append("Waiting for confirmation.")
    else:
        if confidence := sig.get("confidence", 0):
            if confidence < 30:
                parts.append("Low confidence. No clear opportunity.")
            else:
                parts.append("Indicators are mixed. Staying neutral.")

    return " ".join(parts)


def _suggest_action(signal: str, conf: float) -> str:
    if signal == "BUY" and conf >= 70:
        return "\U0001f6a9 Consider BUY entry with confirmation."
    if signal == "BUY":
        return "Monitor for additional confirmation before entry."
    if signal == "SELL" and conf >= 70:
        return "\U0001f6a9 Consider SELL entry with confirmation."
    if signal == "SELL":
        return "Monitor for additional confirmation before entry."
    if signal == "WATCH":
        return "Wait. Monitor for bullish/bearish confirmation."
    if conf < 30:
        return "Ignore. Confidence too low for action."
    return "Wait. No actionable signal."


# ── 5. Decision Path ───────────────────────────────────────────────────


def _decision_paths(pair_signals: dict[str, dict],
                    rejected: list[dict]) -> str:
    lines = ["**Decision Paths**", ""]
    any_path = False

    for pair in config.PAIRS:
        sig = pair_signals.get(pair)
        if sig is None:
            continue
        signal = sig.get("signal", "")
        conf = sig.get("confidence", 0)
        rsi = sig.get("rsi")
        dir_ = _trend_dir(sig)
        any_path = True

        checks = []
        if dir_ in ("bullish", "bearish") and dir_ != "flat":
            checks.append("Trend confirmed \u2705" if
                          (signal in ("BUY", "SELL")) else "Trend confirmed")
        if rsi is not None and not math.isnan(float(rsi)):
            r = float(rsi)
            if (signal == "BUY" and r > 55) or (signal == "SELL" and r < 45):
                checks.append("Momentum confirmed \u2705")
            else:
                checks.append("Momentum noted")
        checks.append(f"Confidence {_int_pct(conf)}")

        lines.append(f"{pair}")
        for c in checks:
            lines.append(f"  \u2022 {c}")

        # Show what rejected this signal's historical counterparts
        pair_rejected = [r for r in rejected
                         if r.get("pair", "").replace("/", "") == pair.replace("/", "")]
        if pair_rejected:
            rej_reasons = set()
            for r in pair_rejected:
                rej_reasons.update(r.get("rejected_by", []))
            if rej_reasons:
                for r_name in sorted(rej_reasons):
                    lines.append(f"  \u26d4 Filtered by {r_name}")
        lines.append(f"**Decision**  {signal}")
        lines.append("")

    if not any_path:
        lines.append("No assets with data to evaluate.")
    return "\n".join(lines)


# ── 6. Signal Reasoning (only for BUY/SELL) ────────────────────────────


def _signal_reasoning(pair_signals: dict[str, dict]) -> str:
    lines = ["**Signal Reasoning**", ""]
    any_signal = False

    for pair in config.PAIRS:
        sig = pair_signals.get(pair)
        if sig is None:
            continue
        signal = sig.get("signal", "")
        if signal not in ("BUY", "SELL"):
            continue
        any_signal = True
        dir_ = _trend_dir(sig)
        conf = sig.get("confidence", 0)
        rsi = sig.get("rsi")

        lines.append(f"{signal} confirmed for {pair} because:")
        if dir_ == "bullish" and signal == "BUY":
            lines.append("  \u2022 Bullish SMA crossover confirmed")
        elif dir_ == "bearish" and signal == "SELL":
            lines.append("  \u2022 Bearish SMA crossover confirmed")
        if rsi is not None and not math.isnan(float(rsi)):
            r = float(rsi)
            if signal == "BUY" and r > 55:
                lines.append("  \u2022 RSI recovered from neutral zone")
            elif signal == "SELL" and r < 45:
                lines.append("  \u2022 RSI entering bearish territory")
        if conf >= 70:
            lines.append("  \u2022 High confidence signal \u2705")
        elif conf >= 40:
            lines.append("  \u2022 Medium confidence \u2014 "
                         "additional confirmation recommended")
        lines.append("")

    if not any_signal:
        lines.append("No BUY or SELL signals triggered today.")
    return "\n".join(lines)


# ── 7. Commentary ──────────────────────────────────────────────────────


def _commentary(pair_signals: dict[str, dict]) -> str:
    mood = _market_mood(pair_signals)
    parts = []
    dir_count = Counter()
    for s in pair_signals.values():
        dir_count[_trend_dir(s)] += 1
    sig_count = Counter(s.get("signal", "") for s in pair_signals.values())
    confs = [float(s.get("confidence", 0)) for s in pair_signals.values()]

    if mood == "bullish":
        parts.append("Markets lean bullish today.")
    elif mood == "bearish":
        parts.append("Markets lean bearish today.")
    else:
        parts.append("Markets are mixed with no dominant direction.")

    for pair in config.PAIRS:
        sig = pair_signals.get(pair)
        if sig is None:
            continue
        dir_ = _trend_dir(sig)
        rsi_note = _rsi_note(sig.get("rsi"))
        token = pair.replace("/", "")

        if dir_ == "bullish":
            parts.append(f"{token} remains in an uptrend"
                         f"{' but momentum is slowing' if 'overbought' in rsi_note else ''}.")
        elif dir_ == "bearish":
            parts.append(f"{token} continues to weaken"
                         f"{' but approaching oversold' if 'oversold' in rsi_note else ''}.")
        else:
            parts.append(f"{token} is consolidating"
                         f"{' with neutral momentum' if rsi_note == 'neutral' else ''}.")

    # Overall assessment
    if confs:
        avg_conf = sum(confs) / len(confs)
        if avg_conf < 30:
            parts.append("Overall the market lacks high-conviction opportunities today.")
        elif avg_conf < 60:
            parts.append("Selective opportunities exist but require patience.")
        else:
            parts.append("Several high-quality setups are present today.")

    return "**Today\u2019s Commentary**\n\n" + " ".join(parts)


# ── 8. Confidence Meter ────────────────────────────────────────────────


def _confidence_meters(pair_signals: dict[str, dict]) -> str:
    lines = ["**Confidence**", ""]
    for pair in config.PAIRS:
        sig = pair_signals.get(pair)
        if sig is None:
            continue
        conf = float(sig.get("confidence", 0))
        level = _confidence_level(conf)
        bar = _conf_bar(conf)
        lines.append(f"{pair:<10s}  {bar}  {_int_pct(conf)}  {level}")
    return "\n".join(lines)


# ── 9. Technical Summary Table ─────────────────────────────────────────


def _summary_table(pair_signals: dict[str, dict]) -> str:
    lines = ["**Technical Summary**", ""]
    header = f"{'Symbol':<10s}  {'Trend':<9s}  {'RSI':<5s}  {'Conf':<5s}  {'Signal':<6s}  Action"
    lines.append(header)
    lines.append("\u2500" * len(header))
    for pair in config.PAIRS:
        sig = pair_signals.get(pair)
        if sig is None:
            lines.append(f"{pair:<10s}  {'—':<9s}  {'—':<5s}  {'—':<5s}  {'—':<6s}  —")
            continue
        dir_ = _trend_dir(sig).capitalize()
        rsi = _fmt(sig.get("rsi"), 0)
        conf = _int_pct(sig.get("confidence", 0))
        signal = sig.get("signal", "—")
        emoji = _visual_signal(signal)
        action = _suggest_action(signal, float(sig.get("confidence", 0)))
        lines.append(f"{pair:<10s}  {dir_:<9s}  {rsi:<5s}  {conf:<5s}  "
                     f"{emoji}{signal:<4s}  {action[:25]}")
    return "\n".join(lines)


# ── 10. Technical Appendix ─────────────────────────────────────────────


def _appendix(pair_signals: dict[str, dict]) -> str:
    lines = ["**Technical Appendix**", ""]
    for pair in config.PAIRS:
        sig = pair_signals.get(pair)
        if sig is None:
            continue
        lines.append(f"{pair}")
        lines.append(f"  SMA20         {_fmt(sig.get('sma_fast'), 5)}")
        lines.append(f"  SMA50         {_fmt(sig.get('sma_slow'), 5)}")
        lines.append(f"  RSI           {_fmt(sig.get('rsi'), 2)}")
        lines.append(f"  ATR           \u2014")
        lines.append(f"  MACD          \u2014")
        lines.append(f"  Confidence    {_int_pct(sig.get('confidence', 0))}")
        lines.append(f"  Latest cross  {sig.get('date', '—')}")
        lines.append("")
    return "\n".join(lines)


# ── 11. Scan Statistics ────────────────────────────────────────────────


def _scan_stats(pair_signals: dict[str, dict],
                rejected: list[dict],
                run_start: float, run_end: float,
                run_stats: dict) -> str:
    lines = ["**Scan Statistics**", ""]
    n_pairs = len(config.PAIRS)
    n_ok = len(pair_signals)
    n_fail = n_pairs - n_ok
    n_rejected = len(rejected)
    elapsed = round(run_end - run_start, 2)

    # Rejection breakdown
    rej_counts: Counter = Counter()
    for r in rejected:
        for fname in r.get("rejected_by", []):
            rej_counts[fname] += 1

    lines.append(f"{'Pairs scanned':26s}  {n_pairs}")
    lines.append(f"{'Pairs with data':26s}  {n_ok}")
    lines.append(f"{'Accepted signals':26s}  {len(pair_signals)}")
    lines.append(f"{'Rejected signals':26s}  {n_rejected}")
    for name, count in rej_counts.most_common():
        label = f"  Rejected by {name}"
        lines.append(f"{label:26s}  {count}")
    lines.append(f"{'Runtime':26s}  {elapsed}s")
    lines.append(f"{'Data source':26s}  yfinance")

    log_path = os.path.join(
        config.LOGS_DIR,
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    )
    lines.append(f"{'Log file':26s}  {log_path}")

    return "\n".join(lines)


# ── 12. Historical Context ─────────────────────────────────────────────


def _historical(pair_signals: dict[str, dict]) -> str:
    """Read today's log file to calculate rolling stats (eventually
    will span multiple days)."""
    log_path = os.path.join(
        config.LOGS_DIR,
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    )
    todays_runs = []
    try:
        with open(log_path) as f:
            todays_runs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    if not todays_runs:
        return "**Historical Context**\n\n_No historical data yet. This section will populate after several runs._"

    # Aggregate over today's runs
    total_accepted = sum(r.get("signals_accepted", 0) for r in todays_runs)
    total_rejected = sum(r.get("signals_rejected", 0) for r in todays_runs)
    all_sigs = []
    for r in todays_runs:
        all_sigs.extend(r.get("latest_signals", []))
        all_sigs.extend(r.get("rejected_signals", []))

    if not all_sigs:
        return "**Historical Context**\n\n_No signal data available yet._"

    confs = [s.get("confidence", 0) for s in all_sigs if isinstance(s.get("confidence"), (int, float))]
    daily_buys = sum(1 for s in all_sigs if s.get("decision") == "BUY")
    daily_sells = sum(1 for s in all_sigs if s.get("decision") == "SELL")
    daily_holds = sum(1 for s in all_sigs if s.get("decision") == "HOLD")
    avg_conf = sum(confs) / len(confs) if confs else 0
    max_conf = max(confs) if confs else 0
    min_conf = min(confs) if confs else 0

    lines = [
        "**Historical Context (Today)**",
        "",
        f"{'BUY signals':20s}  {daily_buys}",
        f"{'SELL signals':20s}  {daily_sells}",
        f"{'HOLD signals':20s}  {daily_holds}",
        f"{'Average confidence':20s}  {avg_conf:.1f}%",
        f"{'Highest confidence':20s}  {max_conf:.1f}%",
        f"{'Lowest confidence':20s}  {min_conf:.1f}%",
    ]
    return "\n".join(lines)


# ── 13. Daily Conclusion ───────────────────────────────────────────────


def _conclusion(pair_signals: dict[str, dict],
                rejected: list[dict]) -> str:
    if not pair_signals:
        return "**Today\u2019s Conclusion**\n\nNo data to evaluate."

    buys = sum(1 for s in pair_signals.values() if s.get("signal") == "BUY")
    sells = sum(1 for s in pair_signals.values() if s.get("signal") == "SELL")
    watches = sum(1 for s in pair_signals.values()
                  if s.get("signal") == "WATCH")
    highs = sum(1 for s in pair_signals.values()
                if s.get("confidence", 0) >= 70)
    mood = _market_mood(pair_signals)

    if buys + sells > 0 and highs > 0:
        return (
            f"**Today\u2019s Conclusion**\n\n"
            f"{buys + sells} high-confidence {'setup' if buys + sells == 1 else 'setups'} detected. "
            f"Review {buys + sells} candidate(s) before market open."
        )
    if watches > 0:
        return (
            "**Today\u2019s Conclusion**\n\n"
            "No confirmed signals, but several assets are approaching thresholds. "
            "Monitor closely."
        )
    if rejected:
        return (
            "**Today\u2019s Conclusion**\n\n"
            f"All signals filtered ({len(rejected)} rejected). "
            "Market conditions do not meet current criteria. "
            "Patience is recommended."
        )
    return (
        "**Today\u2019s Conclusion**\n\n"
        "No strong opportunities. Market remains neutral. "
        "Patience is recommended."
    )


# ── 14. Disclaimer ─────────────────────────────────────────────────────


def _disclaimer() -> str:
    return (
        "\n---\n"
        "\U0001f6a8 *This report provides informational trading signals only.*\n"
        "*No trades are executed automatically.*\n"
        "*Always perform your own analysis before entering a position.*"
    )


# ── Public API ─────────────────────────────────────────────────────────


def format_daily_report(
    pair_signals: dict[str, dict[str, Any]],
    rejected: list[dict[str, Any]],
    run_start: float = 0.0,
    run_end: float = 0.0,
    ranking: list[dict] | None = None,
    top_n_opportunities: int = 3,
) -> str:
    """Build the complete layered daily market briefing.

    Sections are ordered from most actionable to most detailed.  If the
    output exceeds the Telegram character limit, lower-priority sections
    are trimmed from the bottom.

    ``ranking`` is the optional list of ranked assets from
    :func:`ranking.ranker.rank_assets`. When supplied, a "Top
    Opportunities" section is inserted right after the executive
    summary, surfacing the best places to invest today.
    """
    sections: list[tuple[str, int]] = []  # (text, priority) — lower = cut first

    sections.append((_executive_summary(pair_signals, rejected), 1))
    sections.append(("", 0))

    # Change Since Yesterday — inserted right after exec summary if data exists
    change_text = _change_since_yesterday(pair_signals)
    if change_text:
        sections.append((change_text, 1))
        sections.append(("", 0))

    # Top Opportunities from the ranking engine (priority 1 — top of report)
    top_text = _top_opportunities(ranking, top_n=top_n_opportunities)
    if top_text:
        sections.append((top_text, 1))
        sections.append(("", 0))

    sections.append((_opportunity_score(pair_signals), 2))
    sections.append(("", 0))
    sections.append((_heatmap(pair_signals), 3))
    sections.append(("", 0))

    # Asset cards
    for pair in config.PAIRS:
        sig = pair_signals.get(pair)
        card = _asset_card(pair, sig, None)  # No DataFrame passed for now
        sections.append((card, 4))
        sections.append(("", 0))

    sections.append((_decision_paths(pair_signals, rejected), 5))
    sections.append(("", 0))
    sections.append((_signal_reasoning(pair_signals), 6))
    sections.append(("", 0))
    sections.append((_commentary(pair_signals), 7))
    sections.append(("", 0))
    sections.append((_confidence_meters(pair_signals), 8))
    sections.append(("", 0))
    sections.append((_summary_table(pair_signals), 9))
    sections.append(("", 0))
    sections.append((_appendix(pair_signals), 10))
    sections.append(("", 0))
    sections.append((_scan_stats(pair_signals, rejected, run_start, run_end, {}), 11))
    sections.append(("", 0))
    sections.append((_historical(pair_signals), 12))
    sections.append(("", 0))
    sections.append((_conclusion(pair_signals, rejected), 13))
    sections.append(("", 0))
    sections.append((_disclaimer(), 14))

    # Assemble with length management — cut from the back if over limit
    full = ""
    # Keep sections with highest priority first, cut from bottom
    for text, _prio in sections:
        candidate = full + text + "\n" if not full.endswith("\n") else full + text
        if text and len(candidate) > MAX_CHARS:
            break  # stop adding more sections
        full = candidate

    return full.strip()
