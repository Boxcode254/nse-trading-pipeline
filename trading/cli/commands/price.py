"""``trading price SYMBOL`` — latest price + summary stats."""
from __future__ import annotations

from .. import output
from ...services import market


def run(symbol: str, quiet: bool = False, as_json: bool = False, verbose: bool = False, output_path: str | None = None) -> int:
    """Show the latest price + a snapshot for one symbol."""
    snap = market.asset_snapshot(symbol)
    price_info = market.latest_price(symbol)
    result = {**price_info, **snap}
    if as_json:
        print(output.json_dumps(result))
        return 0
    if quiet:
        print(f"{result['symbol']:<8s} {result['price']}  ({result.get('change_pct', 0):+.2f}%)")
        return 0
    print(f"\n{result['symbol']}  ({result.get('date', '?')})")
    if result.get("price") is not None:
        print(f"  Price:               {result['price']}")
    if result.get("change_pct") is not None:
        arrow = "▲" if result["change_pct"] >= 0 else "▼"
        print(f"  Change:              {arrow} {result['change_abs']} ({result['change_pct']:+.2f}%)")
    if result.get("sma_20") is not None:
        print(f"  SMA(20):             {result['sma_20']}")
    if result.get("sma_50") is not None:
        print(f"  SMA(50):             {result['sma_50']}")
    if result.get("trend"):
        print(f"  Trend:               {result['trend']}")
    if result.get("annualised_volatility_pct") is not None:
        print(f"  Volatility (ann.):   {result['annualised_volatility_pct']}%")
    print(f"  Source:              {result.get('source', '?')}")
    return 0
