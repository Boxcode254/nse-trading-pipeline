#!/usr/bin/env python3
"""
NSE Backtester — event-driven backtesting on cached daily OHLCV bars.

Usage:
    python -m trading.backtest.nse_backtest --strategy sma_cross --symbols SCOM,KCB,COOP --start 2025-01-01 --end 2026-07-29
    python -m trading.backtest.nse_backtest --strategy rsi_mean_reversion --symbols SCOM --start 2025-01-01
    python -m trading.backtest.nse_backtest --list-strategies
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Ensure trading package is importable
_TRADING_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _TRADING_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_ROOT)

from trading import config
from trading.fetchers.nse import fetch_data


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class Trade:
    """Single completed trade."""
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    days_held: int
    side: str  # 'long' only for now

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_date": self.entry_date.strftime("%Y-%m-%d"),
            "entry_price": round(self.entry_price, 2),
            "exit_date": self.exit_date.strftime("%Y-%m-%d"),
            "exit_price": round(self.exit_price, 2),
            "shares": self.shares,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "days_held": self.days_held,
            "side": self.side,
        }


@dataclass
class Position:
    """Open position during backtest."""
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    side: str = "long"

    def current_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.shares

    def current_pnl_pct(self, current_price: float) -> float:
        return (current_price - self.entry_price) / self.entry_price * 100


@dataclass
class BacktestResult:
    """Complete backtest results."""
    strategy: str
    symbols: list[str]
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)  # {date, equity}
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_days_held: float = 0.0
    max_concurrent_positions: int = 0
    turnover: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "symbols": self.symbols,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_capital": self.initial_capital,
            "final_capital": round(self.final_capital, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "avg_days_held": round(self.avg_days_held, 1),
            "max_concurrent_positions": self.max_concurrent_positions,
            "turnover": round(self.turnover, 2),
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": self.equity_curve,
        }


# ── Strategy base ──────────────────────────────────────────────────────────

class Strategy:
    """Base strategy class. Subclass and implement generate_signals()."""

    def __init__(self, params: dict = None):
        self.params = params or {}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Generate trading signals for a symbol's DataFrame.

        Returns a Series indexed by date with values:
        - 1 = buy signal
        - -1 = sell signal
        - 0 = hold/no signal
        """
        raise NotImplementedError

    def position_size(self, capital: float, price: float, signal: int) -> int:
        """Determine number of shares to buy. Default: 10% of capital per trade."""
        if signal != 1:
            return 0
        allocation = capital * self.params.get("position_size_pct", 0.10)
        return max(1, int(allocation // price))


# ── Built-in strategies ────────────────────────────────────────────────────

class SMACrossStrategy(Strategy):
    """SMA crossover: buy when fast SMA crosses above slow SMA, sell on cross below."""

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        fast = self.params.get("fast_period", 20)
        slow = self.params.get("slow_period", 50)

        df = df.copy()
        df[f"sma_{fast}"] = df["close"].rolling(fast).mean()
        df[f"sma_{slow}"] = df["close"].rolling(slow).mean()

        signals = pd.Series(0, index=df.index, dtype=int)
        # Buy: fast crosses above slow
        buy_mask = (df[f"sma_{fast}"] > df[f"sma_{slow}"]) & (
            df[f"sma_{fast}"].shift(1) <= df[f"sma_{slow}"].shift(1)
        )
        # Sell: fast crosses below slow
        sell_mask = (df[f"sma_{fast}"] < df[f"sma_{slow}"]) & (
            df[f"sma_{fast}"].shift(1) >= df[f"sma_{slow}"].shift(1)
        )

        signals[buy_mask] = 1
        signals[sell_mask] = -1
        return signals


class RSIStrategy(Strategy):
    """RSI mean reversion: buy oversold (<30), sell overbought (>70)."""

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        period = self.params.get("rsi_period", 14)
        oversold = self.params.get("oversold", 30)
        overbought = self.params.get("overbought", 70)

        df = df.copy()
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))

        signals = pd.Series(0, index=df.index, dtype=int)
        # Buy when RSI crosses up from oversold
        buy_mask = (df["rsi"] > oversold) & (df["rsi"].shift(1) <= oversold)
        # Sell when RSI crosses down from overbought
        sell_mask = (df["rsi"] < overbought) & (df["rsi"].shift(1) >= overbought)

        signals[buy_mask] = 1
        signals[sell_mask] = -1
        return signals


class BreakoutStrategy(Strategy):
    """Donchian channel breakout: buy 20-day high break, sell 10-day low break."""

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        entry_period = self.params.get("entry_period", 20)
        exit_period = self.params.get("exit_period", 10)

        df = df.copy()
        df["upper"] = df["high"].rolling(entry_period).max()
        df["lower"] = df["low"].rolling(exit_period).min()

        signals = pd.Series(0, index=df.index, dtype=int)
        # Buy on close above prior upper
        buy_mask = (df["close"] > df["upper"].shift(1)) & (df["close"].shift(1) <= df["upper"].shift(2))
        # Sell on close below prior lower
        sell_mask = (df["close"] < df["lower"].shift(1)) & (df["close"].shift(1) >= df["lower"].shift(2))

        signals[buy_mask] = 1
        signals[sell_mask] = -1
        return signals


# Strategy registry
STRATEGIES = {
    "sma_cross": SMACrossStrategy,
    "rsi_mean_reversion": RSIStrategy,
    "breakout": BreakoutStrategy,
}


# ── Backtest engine ────────────────────────────────────────────────────────

class BacktestEngine:
    """Event-driven backtest engine."""

    def __init__(
        self,
        strategy: Strategy,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float = 100000.0,
        commission_pct: float = 0.0014,  # 0.14% typical NSE brokerage + fees
        slippage_pct: float = 0.0005,    # 0.05% slippage
        max_positions: int = 10,
    ):
        self.strategy = strategy
        self.symbols = symbols
        self.start_date = pd.Timestamp(start_date)
        self.end_date = pd.Timestamp(end_date)
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.max_positions = max_positions

        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.equity_curve: list[dict] = []
        self.daily_returns: list[float] = []
        self.peak_equity = initial_capital
        self.max_drawdown = 0.0
        self.max_concurrent = 0
        self.turnover = 0.0

    def load_data(self, symbol: str) -> pd.DataFrame:
        """Load cached OHLCV data for a symbol."""
        df = fetch_data(symbol, days=1000)  # Get max available
        if df is None or df.empty:
            return pd.DataFrame()

        # Filter date range
        df = df[(df.index >= self.start_date) & (df.index <= self.end_date)]
        return df

    def run(self) -> BacktestResult:
        """Execute the backtest."""
        print(f"🔄 Backtesting {self.strategy.__class__.__name__} on {len(self.symbols)} symbols...")
        print(f"   Period: {self.start_date.date()} to {self.end_date.date()}")
        print(f"   Capital: KES {self.initial_capital:,.2f}")

        # Load all data first
        all_data = {}
        for sym in self.symbols:
            df = self.load_data(sym)
            if df.empty:
                print(f"   ⚠️  {sym}: no data")
                continue
            # Generate signals
            df = df.copy()
            df["signal"] = self.strategy.generate_signals(df)
            all_data[sym] = df
            print(f"   ✓ {sym}: {len(df)} bars, {(df['signal']==1).sum()} buys, {(df['signal']==-1).sum()} sells")

        if not all_data:
            raise ValueError("No data loaded for any symbol")

        # Get unified date index (union of all trading days)
        all_dates = sorted(set().union(*[df.index for df in all_data.values()]))
        all_dates = [d for d in all_dates if self.start_date <= d <= self.end_date]

        # Main event loop
        prev_equity = self.initial_capital
        for date in all_dates:
            # 1. Process exits first (sell signals)
            for sym, df in all_data.items():
                if date not in df.index:
                    continue
                if sym not in self.positions:
                    continue

                row = df.loc[date]
                signal = row.get("signal", 0)
                price = float(row["close"])

                if signal == -1:  # Sell signal
                    pos = self.positions.pop(sym)
                    exec_price = price * (1 - self.slippage_pct)
                    commission = exec_price * pos.shares * self.commission_pct
                    pnl = (exec_price - pos.entry_price) * pos.shares - commission
                    pnl_pct = (exec_price - pos.entry_price) / pos.entry_price * 100

                    trade = Trade(
                        symbol=sym,
                        entry_date=pos.entry_date,
                        entry_price=pos.entry_price,
                        exit_date=date,
                        exit_price=exec_price,
                        shares=pos.shares,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        days_held=(date - pos.entry_date).days,
                        side=pos.side,
                    )
                    self.trades.append(trade)
                    self.cash += exec_price * pos.shares - commission
                    self.turnover += exec_price * pos.shares

            # 2. Process entries (buy signals)
            for sym, df in all_data.items():
                if date not in df.index:
                    continue
                if sym in self.positions:
                    continue  # Already have position
                if len(self.positions) >= self.max_positions:
                    continue  # Position limit reached

                row = df.loc[date]
                signal = row.get("signal", 0)
                price = float(row["close"])

                if signal == 1:  # Buy signal
                    shares = self.strategy.position_size(self.cash, price, signal)
                    if shares == 0:
                        continue

                    exec_price = price * (1 + self.slippage_pct)
                    cost = exec_price * shares
                    commission = cost * self.commission_pct
                    total_cost = cost + commission

                    if total_cost > self.cash:
                        # Scale down to available cash
                        max_shares = int((self.cash / (1 + self.commission_pct)) // exec_price)
                        if max_shares < 1:
                            continue
                        shares = max_shares
                        cost = exec_price * shares
                        commission = cost * self.commission_pct
                        total_cost = cost + commission

                    self.cash -= total_cost
                    self.turnover += cost
                    self.positions[sym] = Position(
                        symbol=sym,
                        entry_date=date,
                        entry_price=exec_price,
                        shares=shares,
                    )
                    self.max_concurrent = max(self.max_concurrent, len(self.positions))

            # 3. Mark-to-market equity
            equity = self.cash
            for sym, pos in self.positions.items():
                if sym in all_data and date in all_data[sym].index:
                    price = float(all_data[sym].loc[date]["close"])
                    equity += price * pos.shares

            self.equity_curve.append({"date": date.strftime("%Y-%m-%d"), "equity": round(equity, 2)})
            daily_ret = (equity - prev_equity) / prev_equity if prev_equity > 0 else 0
            self.daily_returns.append(daily_ret)
            prev_equity = equity

            # Track drawdown
            if equity > self.peak_equity:
                self.peak_equity = equity
            dd = (self.peak_equity - equity) / self.peak_equity * 100
            if dd > self.max_drawdown:
                self.max_drawdown = dd

        # Close any remaining positions at final price
        final_date = all_dates[-1] if all_dates else self.end_date
        for sym, pos in list(self.positions.items()):
            if sym in all_data and final_date in all_data[sym].index:
                price = float(all_data[sym].loc[final_date]["close"])
            else:
                # Use last available price
                price = pos.entry_price
            exec_price = price * (1 - self.slippage_pct)
            commission = exec_price * pos.shares * self.commission_pct
            pnl = (exec_price - pos.entry_price) * pos.shares - commission
            pnl_pct = (exec_price - pos.entry_price) / pos.entry_price * 100

            trade = Trade(
                symbol=sym,
                entry_date=pos.entry_date,
                entry_price=pos.entry_price,
                exit_date=final_date,
                exit_price=exec_price,
                shares=pos.shares,
                pnl=pnl,
                pnl_pct=pnl_pct,
                days_held=(final_date - pos.entry_date).days,
                side=pos.side,
            )
            self.trades.append(trade)
            self.cash += exec_price * pos.shares - commission
            self.turnover += exec_price * pos.shares

        final_equity = self.cash  # All positions closed

        # Calculate metrics
        returns = np.array(self.daily_returns)
        sharpe = 0.0
        if len(returns) > 1 and returns.std() > 0:
            sharpe = returns.mean() / returns.std() * np.sqrt(252)

        winning = [t for t in self.trades if t.pnl > 0]
        losing = [t for t in self.trades if t.pnl <= 0]
        total_trades = len(self.trades)
        win_rate = len(winning) / total_trades * 100 if total_trades > 0 else 0
        avg_win = np.mean([t.pnl for t in winning]) if winning else 0
        avg_loss = np.mean([t.pnl for t in losing]) if losing else 0
        gross_profit = sum(t.pnl for t in winning)
        gross_loss = abs(sum(t.pnl for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        avg_days = np.mean([t.days_held for t in self.trades]) if self.trades else 0

        return BacktestResult(
            strategy=self.strategy.__class__.__name__,
            symbols=self.symbols,
            start_date=self.start_date.strftime("%Y-%m-%d"),
            end_date=self.end_date.strftime("%Y-%m-%d"),
            initial_capital=self.initial_capital,
            final_capital=final_equity,
            total_return_pct=(final_equity - self.initial_capital) / self.initial_capital * 100,
            trades=self.trades,
            equity_curve=self.equity_curve,
            max_drawdown_pct=self.max_drawdown,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            profit_factor=profit_factor if profit_factor != float('inf') else 999.0,
            total_trades=total_trades,
            winning_trades=len(winning),
            losing_trades=len(losing),
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_days_held=avg_days,
            max_concurrent_positions=self.max_concurrent,
            turnover=self.turnover,
        )


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NSE Backtester")
    parser.add_argument("--strategy", default="sma_cross", choices=list(STRATEGIES.keys()) + ["all"],
                        help="Strategy to run")
    parser.add_argument("--symbols", default="SCOM,KCB,COOP,ABSA,EABL,EQTY,KPLC,SCBK,TOTL,KNRE",
                        help="Comma-separated symbols")
    parser.add_argument("--start", default="2025-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD), default=today")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital")
    parser.add_argument("--commission", type=float, default=0.0014, help="Commission % (e.g. 0.0014 = 0.14%)")
    parser.add_argument("--slippage", type=float, default=0.0005, help="Slippage % (e.g. 0.0005 = 0.05%)")
    parser.add_argument("--max-positions", type=int, default=10, help="Max concurrent positions")
    parser.add_argument("--params", default="{}", help="Strategy params as JSON, e.g. '{\"fast_period\":10,\"slow_period\":30}'")
    parser.add_argument("--list-strategies", action="store_true", help="List available strategies")
    parser.add_argument("--output", help="Output JSON file path")

    args = parser.parse_args()

    if args.list_strategies:
        print("Available strategies:")
        for name, cls in STRATEGIES.items():
            print(f"  {name}: {cls.__doc__}")
        return

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    end_date = args.end or datetime.now().strftime("%Y-%m-%d")
    params = json.loads(args.params)

    if args.strategy == "all":
        # Run all strategies and compare
        results = {}
        for name, cls in STRATEGIES.items():
            strategy = cls(params)
            engine = BacktestEngine(
                strategy=strategy,
                symbols=symbols,
                start_date=args.start,
                end_date=end_date,
                initial_capital=args.capital,
                commission_pct=args.commission,
                slippage_pct=args.slippage,
                max_positions=args.max_positions,
            )
            result = engine.run()
            results[name] = result.to_dict()
            print(f"\n{name}: Return={result.total_return_pct:.2f}%, Sharpe={result.sharpe_ratio:.2f}, "
                  f"MaxDD={result.max_drawdown_pct:.2f}%, Trades={result.total_trades}, WinRate={result.win_rate:.1f}%")
        if args.output:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2)
        return

    # Single strategy
    strategy_cls = STRATEGIES[args.strategy]
    strategy = strategy_cls(params)

    engine = BacktestEngine(
        strategy=strategy,
        symbols=symbols,
        start_date=args.start,
        end_date=end_date,
        initial_capital=args.capital,
        commission_pct=args.commission,
        slippage_pct=args.slippage,
        max_positions=args.max_positions,
    )

    result = engine.run()

    # Print summary
    print(f"\n{'='*60}")
    print(f"BACKTEST RESULT — {result.strategy}")
    print(f"{'='*60}")
    print(f"Period:         {result.start_date} to {result.end_date}")
    print(f"Symbols:        {', '.join(result.symbols)}")
    print(f"Initial Capital: KES {result.initial_capital:,.2f}")
    print(f"Final Capital:   KES {result.final_capital:,.2f}")
    print(f"Total Return:    {result.total_return_pct:.2f}%")
    print(f"Max Drawdown:    {result.max_drawdown_pct:.2f}%")
    print(f"Sharpe Ratio:    {result.sharpe_ratio:.2f}")
    print(f"Win Rate:        {result.win_rate:.1f}%")
    print(f"Profit Factor:   {result.profit_factor:.2f}")
    print(f"Total Trades:    {result.total_trades}")
    print(f"  Winning:       {result.winning_trades}")
    print(f"  Losing:        {result.losing_trades}")
    print(f"Avg Win:         KES {result.avg_win:,.2f}")
    print(f"Avg Loss:        KES {result.avg_loss:,.2f}")
    print(f"Avg Days Held:   {result.avg_days_held:.1f}")
    print(f"Max Concurrent:  {result.max_concurrent_positions}")
    print(f"Turnover:        KES {result.turnover:,.2f}")

    if result.trades:
        print(f"\nRecent trades:")
        for t in result.trades[-10:]:
            print(f"  {t.symbol}  {t.entry_date.date()}→{t.exit_date.date()}  "
                  f"{t.shares}@{t.entry_price:.2f}→{t.exit_price:.2f}  "
                  f"P&L={t.pnl:,.0f} ({t.pnl_pct:+.1f}%)  {t.days_held}d")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\n💾 Results saved to {args.output}")


if __name__ == "__main__":
    main()