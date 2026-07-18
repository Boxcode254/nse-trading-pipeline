"""``trading explain SYMBOL`` — plain-English reasoning.

Calls the Investment Advisor to assemble a paragraph that
answers the four spec questions (what happened, why it matters,
should I act, what are the risks) in plain English.
"""
from __future__ import annotations

from .. import output
from ... import config
from ...services import advisor, ranking as ranking_svc, signal as signal_svc
from ...signals import engine as signal_engine
from ...signals import validator as signal_validator
from ...fetchers import fetch_data


def run(symbol: str, quiet: bool = False, as_json: bool = False,
        verbose: bool = False, output_path: str | None = None) -> int:
    """Show a plain-English explanation for one symbol.

    Behaviour:
    - In --json mode, emits the full advisor dict (for dashboards).
    - In quiet mode, prints the plain-language paragraph only.
    - With --verbose, appends raw indicator values to the paragraph.
    - Otherwise, prints the paragraph followed by a one-line score footer.
    """
    pair = symbol  # in this codebase, the symbol IS the pair
    ranked, pair_signals = _gather_context(pair)

    explanation = advisor.explain_symbol(
        symbol=symbol,
        ranked=ranked,
        pair_signals=pair_signals,
        verbose=verbose,
    )

    if as_json:
        # Build a structured dict for the JSON consumer
        entry = next(
            (e for e in ranked if e.get("symbol") == symbol),
            {"symbol": symbol, "score": 0.0,
             "recommendation": config.TIER_HOLD, "factors": {}},
        )
        sig = pair_signals.get(symbol, {})
        print(output.json_dumps({
            "symbol": symbol,
            "score": entry.get("score", 0.0),
            "recommendation": entry.get("recommendation", config.TIER_HOLD),
            "explanation": explanation,
            "rsi": sig.get("rsi"),
            "signal": sig.get("signal", "HOLD"),
            "confidence": sig.get("confidence"),
        }))
        return 0

    if quiet:
        print(explanation)
        return 0

    # Default: print the explanation, then a concise score footer
    entry = next(
        (e for e in ranked if e.get("symbol") == symbol),
        None,
    )
    print(f"\n{explanation}")
    if entry is not None:
        print(f"\n  Score: {entry.get('score', 0.0)}/100 — "
              f"{entry.get('recommendation', config.TIER_HOLD)}")
    return 0


def _gather_context(pair: str) -> tuple[list[dict], dict[str, dict]]:
    """Best-effort fetch of ranking + per-pair signal data for the advisor.

    The advisor wants both: ranking entries (for score/tier/factors) and
    the latest per-pair signal (for RSI/confidence). We build the signal
    dict inline so the explain command works even if the full scan hasn't
    run today.
    """
    pair_signals: dict[str, dict] = {}

    # Try to fetch + analyse the requested pair directly
    try:
        df = fetch_data(pair)
        if df is not None and not df.empty:
            signals = signal_engine.generate_signals(df, pair=pair)
            accepted, _ = signal_validator.filter_signals(signals, df)
            if accepted:
                pair_signals[pair] = accepted[-1]
    except Exception:  # noqa: BLE001
        # Fall back to whatever the signal service can give us
        try:
            sig = signal_svc.signal_for_symbol(pair)
            pair_signals[pair] = {
                "pair": pair,
                "signal": sig.get("indicators", {}).get("signal", "HOLD"),
                "rsi": sig.get("indicators", {}).get("rsi"),
                "confidence": sig.get("confidence"),
            }
        except Exception:  # noqa: BLE001
            pass

    # Build the ranking
    try:
        result = ranking_svc.build()
        ranked = result.get("ranked", [])
    except Exception:  # noqa: BLE001
        ranked = []

    return ranked, pair_signals
