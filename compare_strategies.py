"""Backtest all strategies on all NSE stocks and print comparison."""
import sys, json, os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path.home() / ".trading"))

from trading.strategies import REGISTRY
from trading.backtest.engine import run_backtest
import pandas as pd

# Load real NSE data
DATA_DIR = Path.home() / ".trading/data"
stocks = [f.stem.replace("nse_", "") for f in sorted(DATA_DIR.glob("nse_*.csv"))]
# Filter to stocks with enough data (50+ rows)
valid_stocks = []
for sym in stocks:
    csv = DATA_DIR / f"nse_{sym}.csv"
    df = pd.read_csv(csv, parse_dates=["date"])
    if len(df) >= 50:
        valid_stocks.append(sym)

print(f"Stocks with 50+ data rows: {len(valid_stocks)}/{len(stocks)}")
print(f"Stocks: {', '.join(valid_stocks)}")
print()

results = {}
for key, strategy in REGISTRY.items():
    print(f"\n{'='*60}")
    print(f"Strategy {key}: {strategy.name}")
    print(f"{'='*60}")
    strategy_results = []
    
    for sym in valid_stocks:
        csv = DATA_DIR / f"nse_{sym}.csv"
        df = pd.read_csv(csv, parse_dates=["date"])
        df = df.set_index("date")
        df = df.sort_index()
        
        if len(df) < 50:
            continue
            
        try:
            result = run_backtest(sym, df, strategy=strategy)
            strategy_results.append({
                "symbol": sym,
                "trades": result.total_trades,
                "win_rate": result.win_rate_pct,
                "total_return": result.total_return_pct,
                "annual_return": result.annualised_return_pct,
                "max_dd": result.max_drawdown_pct,
                "sharpe": result.sharpe_ratio,
                "buy_hold": result.buy_and_hold_return_pct,
                "capture_ratio": (result.total_return_pct / result.buy_and_hold_return_pct * 100) if result.buy_and_hold_return_pct else 0,
            })
            print(f"  {sym:6s}: trades={result.total_trades:3d}  return={result.total_return_pct:+6.1f}%  "
                  f"BH={result.buy_and_hold_return_pct:+6.1f}%  capture={result.total_return_pct/result.buy_and_hold_return_pct*100 if result.buy_and_hold_return_pct else 0:5.0f}%  "
                  f"wr={result.win_rate_pct:4.0f}%  dd={result.max_drawdown_pct:5.1f}%  sharpe={result.sharpe_ratio:+5.2f}")
        except Exception as e:
            print(f"  {sym:6s}: ERROR — {e}")
    
    if strategy_results:
        avg_return = sum(r["total_return"] for r in strategy_results) / len(strategy_results)
        avg_capture = sum(r["capture_ratio"] for r in strategy_results) / len(strategy_results)
        avg_sharpe = sum(r["sharpe"] for r in strategy_results) / len(strategy_results)
        total_trades = sum(r["trades"] for r in strategy_results)
        print(f"  AVERAGE: return={avg_return:+6.1f}%  capture={avg_capture:5.0f}%  "
              f"sharpe={avg_sharpe:+5.2f}  trades={total_trades}")
    
    results[key] = {"name": strategy.name, "stocks": strategy_results}

# Summary ranking
print(f"\n\n{'='*60}")
print("RANKING: Average Buy-and-Hold Capture Rate")
print(f"{'='*60}")
ranked = []
for key, data in results.items():
    if data["stocks"]:
        avg = sum(r["capture_ratio"] for r in data["stocks"]) / len(data["stocks"])
        ranked.append((key, data["name"], avg))
ranked.sort(key=lambda x: -x[2])
for i, (key, name, cap) in enumerate(ranked):
    print(f"  {i+1}. {key}: {name} — {cap:.0f}% BH capture")
