"""``trading morning`` — complete morning briefing.

Uses the Investment Advisor to assemble the full Daily Investment
Brief from per-pair signals and the ranking engine's output.
The CLI itself contains no narrative logic — everything comes
from the advisor.

Sections (top to bottom)
------------------------
1. Header (date, mood, opportunity score)
2. Market summary
3. Top opportunities
4. Assets to avoid
5. Portfolio suggestion
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

from rich.console import Console

from .. import output
from ... import config
from ...fetchers import fetch_data
from ...services import advisor, ranking
from ...signals import engine as signal_engine
from ...signals import validator as signal_validator
from ...storage import log as storage_log


def run(
    telegram: bool = False,
    save: bool = False,
    output_path: str | None = None,
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Build and print the morning briefing.

    ``telegram=True`` produces a single 4096-char block suitable
    for delivery via Telegram. ``save=True`` writes a copy to
    ``output_path`` (default: ``~/.trading/logs/morning-YYYY-MM-DD.md``).
    """
    config.ensure_dirs()

    # 1) Run the scan — produce per-asset signals
    pair_signals, rejected = _scan_pairs()

    # 2) Build the ranking
    ranking_result = ranking.build()
    ranked = ranking_result.get("ranked", [])

    # 3) Compute learning integration: total opportunities + strongest pick
    opportunities_analyzed = len(pair_signals)
    strongest_symbol = None
    strongest_confidence = None
    if ranked and pair_signals:
        top_entry = ranked[0]
        strongest_symbol = top_entry.get("symbol")
        sig = pair_signals.get(strongest_symbol)
        if sig:
            strongest_confidence = sig.get("confidence")

    # 4) Hand everything to the advisor
    date = _today()
    body = advisor.daily_brief(
        date=date,
        ranked=ranked,
        pair_signals=pair_signals,
        opportunities_analyzed=opportunities_analyzed,
        strongest_symbol=strongest_symbol,
        strongest_confidence=strongest_confidence,
    )

    if as_json:
        market_score = _market_opportunity_score(ranked)
        print(output.json_dumps({
            "date": date,
            "market_opportunity_score": market_score,
            "pair_signals": pair_signals,
            "ranked": ranked,
            "rejected_count": len(rejected),
            "opportunities_analyzed": opportunities_analyzed,
            "strongest_symbol": strongest_symbol,
            "strongest_confidence": strongest_confidence,
            "brief": body,
        }))
        return 0

    if telegram:
        # Telegram mode: single block, no rich console
        print(body)
    elif quiet:
        print(body)
    else:
        console = Console()
        console.print(body)

    if save or output_path:
        path = output_path or os.path.join(
            config.LOGS_DIR,
            f"morning-{_today()}.md",
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(body)
        except OSError as exc:
            print(f"⚠️  Could not save briefing: {exc}", file=sys.stderr)
            return 1
    return 0 if pair_signals else 1


# ── Helpers ────────────────────────────────────────────────────────


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _market_opportunity_score(ranked: list[dict]) -> float:
    if not ranked:
        return 0.0
    return round(sum(r["score"] for r in ranked) / len(ranked), 1)


def _scan_pairs() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Run signal generation + validation over every configured pair."""
    pair_signals: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for pair in config.PAIRS:
        try:
            df = fetch_data(pair)
        except Exception:  # noqa: BLE001
            continue
        try:
            signals = signal_engine.generate_signals(df, pair=pair)
        except Exception:  # noqa: BLE001
            continue
        if not signals:
            continue
        accepted, pair_rejected = signal_validator.filter_signals(signals, df)
        rejected.extend(pair_rejected)
        if not accepted:
            continue
        current = accepted[-1]
        try:
            storage_log.log_signal(current)
        except Exception:  # noqa: BLE001
            pass
        pair_signals[pair] = current
    return pair_signals, rejected
