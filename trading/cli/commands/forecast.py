"""Forecast command — statistical price projection for NSE stocks.

Uses recent trend + volatility to project a price range for the
next 1-7 trading days. Not machine learning — just math that works.

Method: Take the 20-day price history, fit a linear trend, then
project forward with ±1σ and ±2σ bands based on recent volatility.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Optional

import numpy as np


def forecast_price(
    closes: list[float],
    volatility_annual_pct: float,
    days: int = 5,
) -> dict:
    """Project price range for the next *days* trading days.

    Args:
        closes: Recent close prices (most recent last), at least 5 entries.
        volatility_annual_pct: Annualised volatility (e.g. 21.41 for 21.41%).
        days: Number of trading days to project forward (1-7).

    Returns:
        Dict with central projection, low/high bands, and confidence labels.
    """
    if len(closes) < 5:
        return {"error": "Need at least 5 days of price history"}

    days = max(1, min(7, days))

    # Convert annual vol to daily vol (≈252 trading days)
    daily_vol = (volatility_annual_pct / 100) / np.sqrt(252)

    # Fit linear trend over recent window
    x = np.arange(len(closes))
    y = np.array(closes, dtype=float)
    coeffs = np.polyfit(x, y, 1)
    trend_daily = coeffs[0]  # KES per day

    # Base projection: last close + trend * days
    last_close = closes[-1]
    central = last_close + trend_daily * days

    # Uncertainty bands
    sigma = last_close * daily_vol * np.sqrt(days)
    bands = {
        "upper_2σ": round(central + 2 * sigma, 2),
        "upper_1σ": round(central + sigma, 2),
        "central": round(central, 2),
        "lower_1σ": round(central - sigma, 2),
        "lower_2σ": round(central - 2 * sigma, 2),
    }

    # Confidence label
    if volatility_annual_pct < 15:
        confidence = "high"    # low vol → tighter bands
    elif volatility_annual_pct < 30:
        confidence = "medium"
    else:
        confidence = "low"     # high vol → wide bands

    return {
        "symbol": None,  # filled by caller
        "last_price": round(last_close, 2),
        "trend_daily_kes": round(trend_daily, 4),
        "volatility_annual_pct": round(volatility_annual_pct, 2),
        "days_forward": days,
        "projection": bands,
        "confidence": confidence,
        "method": "linear_trend + 1σ/2σ bands",
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    """CLI wrapper — called from `trading forecast SYMBOL [--days N]`."""
    import argparse
    import json
    import os
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Statistical price forecast for NSE stocks"
    )
    parser.add_argument("symbol", help="Stock symbol (e.g. SCOM)")
    parser.add_argument(
        "--days", type=int, default=5,
        help="Trading days to project (1-7, default 5)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )
    args = parser.parse_args()

    # Get price history and volatility from trading CLI
    import subprocess
    trading_root = Path(os.environ.get("TRADING_ROOT", Path.home() / ".trading"))

    # 1. Get volatility and recent data via 'trading price --verbose'
    result = subprocess.run(
        [sys.executable, "-m", "trading.cli.main", "price", args.symbol,
         "--verbose", "--json"],
        cwd=str(trading_root),
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(json.dumps({"error": f"Failed to fetch price data for {args.symbol}"}))
        return 1

    price_data = json.loads(result.stdout)
    vol = price_data.get("annualised_volatility_pct", 25.0)
    last = price_data.get("price")

    # 2. Get recent closes from the CSV
    import csv
    csv_path = trading_root / "data" / f"nse_{args.symbol}.csv"
    closes = []
    if csv_path.exists():
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        for row in rows[-20:]:  # last 20 days
            val = row.get("close", row.get("Close"))
            if val:
                try:
                    closes.append(float(val))
                except ValueError:
                    pass

    if not closes and last:
        closes = [last]  # fallback: just today

    if len(closes) < 5:
        print(json.dumps({
            "error": f"Only {len(closes)} days of history available (need 5+)",
            "symbol": args.symbol,
        }))
        return 1

    forecast = forecast_price(closes, vol, days=args.days)
    forecast["symbol"] = args.symbol

    if args.json:
        print(json.dumps(forecast, indent=2))
        return 0

    # Human-readable output
    b = forecast["projection"]
    arrow = "▲" if forecast["trend_daily_kes"] > 0 else "▼"
    print(f"\n📊 {args.symbol} Forecast — {args.days}-day projection")
    print(f"   Method: {forecast['method']}")
    print(f"   Confidence: {forecast['confidence']} "
          f"(volatility {forecast['volatility_annual_pct']}%)")
    print()
    print(f"   Last price: KES {forecast['last_price']:.2f}")
    print(f"   Trend:      {arrow} KES {abs(forecast['trend_daily_kes']):.2f}/day")
    print()
    print(f"   ━━━ Projected range ━━━")
    print(f"   ██ Upper 2σ:   KES {b['upper_2σ']:.2f}  (95% range)")
    print(f"   ██ Upper 1σ:   KES {b['upper_1σ']:.2f}  (68% range)")
    print(f"   ██ Central:    KES {b['central']:.2f}")
    print(f"   ██ Lower 1σ:   KES {b['lower_1σ']:.2f}  (68% range)")
    print(f"   ██ Lower 2σ:   KES {b['lower_2σ']:.2f}  (95% range)")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
