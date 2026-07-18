"""``trading signal SYMBOL`` — recommendation + plain-language summary.

The numeric recommendation is the existing signal-service output.
The plain-language portion comes from the Investment Advisor.
"""
from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .. import output
from ... import config
from ...services import advisor, signal as signal_svc


def run(symbol: str, quiet: bool = False, as_json: bool = False, verbose: bool = False, output_path: str | None = None) -> int:
    """Show the recommendation + a one-paragraph explanation for one symbol.

    The rich console section is the legacy view (price, RSI, signal
    type, source). The advisor's natural-language paragraph is printed
    underneath so the user gets both — numbers and meaning.
    """
    result = signal_svc.signal_for_symbol(symbol)

    # Pull the ranking + pair signal once so both the JSON and the
    # console paths can hand a fully-populated context to the advisor.
    from ...services import ranking as ranking_svc
    from ...signals import engine as signal_engine
    from ...signals import validator as signal_validator
    from ...fetchers import fetch_data
    try:
        result_for_advisor = ranking_svc.build()
        ranked = result_for_advisor.get("ranked", [])
    except Exception:  # noqa: BLE001
        ranked = []
    pair_signals: dict[str, dict] = {}
    try:
        df = fetch_data(symbol)
        if df is not None and not df.empty:
            signals = signal_engine.generate_signals(df, pair=symbol)
            accepted, _ = signal_validator.filter_signals(signals, df)
            if accepted:
                pair_signals[symbol] = accepted[-1]
    except Exception:  # noqa: BLE001
        pass

    explanation = advisor.explain_symbol(
        symbol=symbol, ranked=ranked, pair_signals=pair_signals,
    )

    if as_json:
        # Enrich the JSON with the advisor's plain-language paragraph
        result["explanation"] = explanation
        print(output.json_dumps(result))
        return 0

    if quiet:
        # One-line summary, no advisor paragraph (keeps scripting fast)
        print(f"{result['symbol']:<8s} {result['score']:5.1f}  {result['recommendation']}")
        return 0

    console = Console()
    rec = result["recommendation"]
    score = result["score"]
    ind = result.get("indicators", {})

    tier_emoji = {
        "Strong Accumulate": "🟢",
        "Accumulate": "🟩",
        "Hold": "🟡",
        "Reduce": "🟠",
        "Avoid": "🔴",
    }.get(rec, "⚪")
    console.print(f"\n[bold]{symbol}[/]  {tier_emoji}  [bold]{rec}[/]  (score: {score}/100)")

    if ind.get("price") is not None:
        console.print(f"  Price:    {ind['price']}")
    if ind.get("rsi") is not None:
        console.print(f"  RSI(14):  {ind['rsi']:.1f}")
    if ind.get("sma_fast") is not None:
        console.print(f"  SMA(20):  {ind['sma_fast']}")
    if ind.get("sma_slow") is not None:
        console.print(f"  SMA(50):  {ind['sma_slow']}")
    console.print(f"  Signal:   {ind.get('signal', 'HOLD')}")
    console.print(f"  Source:   {result.get('source', '?')}")

    console.print("\n[bold]In plain English:[/]")
    console.print(f"  {explanation}")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(f"{symbol}  {rec}  (score: {score}/100)\n")
            f.write(f"  Price:    {ind.get('price', '?')}\n")
            if ind.get("rsi") is not None:
                f.write(f"  RSI(14):  {ind['rsi']:.1f}\n")
            f.write(f"  Signal:   {ind.get('signal', 'HOLD')}\n")
            f.write(f"\n{explanation}\n")
    return 0