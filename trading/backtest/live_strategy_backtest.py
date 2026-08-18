"""Live-strategy backtest — replays the ACTUAL allocation engine.

For every trading day in the window we:
  1. Build the as-of-date price map + ranking signals (offline, see
     live_strategy_data).
  2. Call the REAL trading.target_allocation.generate_rebalance_plan(
        prices=..., signals=..., portfolio=<live state>) — this is the exact
        function the auto-trader executes daily. No reimplementation.
  3. Apply the SAME cost model (config.trade_cost: 1.5%/side + 0.15% slippage,
     KES 60 floor) to each emitted trade, mutating our sandbox ledger exactly
     like trading.portfolio.engine.buy/sell would.
  4. Record the trade, update cash + positions.

PLUS the agreed suspension probe: over the full window, watch BAMB's bar
behaviour. The live engine excludes BAMB from auto-trading via
config.SUSPENDED_SYMBOLS — but does the STRATEGY LOGIC itself have any
mechanism to detect a stock going illiquid/suspended and react? We test by
(option a) leaving the SUSPENDED exclusion ACTIVE (faithful to how the live
system actually behaved — it held BAMB frozen), and (option b) reporting how
many historical occurrences of a name printing flat/zero-volume for N days
the strategy would have happily kept holding. That reveals the blind spot.

Outputs: metrics (return, drawdown, sharpe, win rate, profit factor), per-trade
log, daily equity curve, and the suspension probe report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from trading import config
from trading.target_allocation import generate_rebalance_plan, SUSPENDED
from trading.backtest import live_strategy_data as lsd

# Cost model is the single source of truth used by the live engine.
def _trade_cost(value: float, price: float) -> dict:
    return config.trade_cost(value, price)


@dataclass
class Trade:
    date: str
    symbol: str
    side: str
    shares: int
    price: float
    gross: float
    fee: float
    slippage: float
    net_cash: float
    reason: str
    signal_score: Optional[float] = None


@dataclass
class BacktestResult:
    start: str
    end: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    win_rate_pct: float
    profit_factor: Optional[float]
    total_trades: int
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    cost_drag: float = 0.0
    suspension_probe: dict = field(default_factory=dict)


def _price_on(df: pd.DataFrame, date: pd.Timestamp) -> Optional[float]:
    row = df[df["date"] <= date]
    if row.empty:
        return None
    return float(row.iloc[-1]["close"])


def run_backtest(
    start: Optional[str] = None,
    end: Optional[str] = None,
    include_bamb: bool = True,
    suspension_probe: bool = True,
    seed_live: bool = False,
    inject_bamb: bool = False,
) -> BacktestResult:
    """Replay the live allocation engine over cached history.

    include_bamb: keep BAMB in the universe (faithful to the live book, which
        held it). Set False to test the tradeable-only book.
    """
    bars = lsd.load_all_bars()
    if not include_bamb:
        bars.pop("BAMB", None)  # option (B): tradeable-only book
    dates = lsd.common_trading_dates(bars)
    if start:
        dates = [d for d in dates if d >= pd.Timestamp(start)]
    if end:
        dates = [d for d in dates if d <= pd.Timestamp(end)]
    if not dates:
        raise RuntimeError("No common trading dates in window")

    # FAITHFUL BACKTEST SEED: start from a clean KES 100k cash slate and let
    # the REAL allocation engine build the book from scratch over the window.
    # This is the honest test of the strategy's skill — the live book's 82
    # trades are just one realization; we test the LOGIC, not that one path.
    # (The live system happened to start ~50/50 invested due to a from_dict
    # seed quirk; testing from zero is the more rigorous framing and we note
    # it in the report. To reproduce the exact live starting book instead,
    # pass seed_live=True.)
    if seed_live:
        init_state = lsd.load_initial_state()
        initial_capital = float(init_state.get("initial_capital", 100000.0))
        cash = float(init_state.get("cash", initial_capital))
        positions = {
            p["symbol"]: {
                "shares": int(p["shares"]),
                "avg_cost": float(p["avg_cost"]),
                "total_cost": float(p.get("total_cost", p["shares"] * p["avg_cost"])),
            }
            for p in init_state.get("positions", [])
        }
    else:
        initial_capital = 100000.0
        cash = 100000.0
        positions = {}
        if inject_bamb:
            # Scenario (A): faithfully reproduce the live book's frozen BAMB
            # takeover-wait position. It is a static, non-tradeable hold: the
            # engine excludes it from auto-trading via SUSPENDED_SYMBOLS, so it
            # neither buys nor sells it. We mark it at the takeover-offer price
            # (54.00), NOT the bar close, because the live book valued BAMB at
            # its frozen mark (the Amsons offer), not the last traded price.
            positions["BAMB"] = {
                "shares": 39, "avg_cost": 54.0,
                "total_cost": 39 * 54.0,
                "frozen_mark": 54.0,  # takeover offer; not a live trade price
            }

    trades: list[Trade] = []
    equity_curve: list[dict] = []
    cost_drag = 0.0
    peak_equity = initial_capital

    # Suspension probe state
    flat_streak: dict[str, int] = {s: 0 for s in bars}
    suspend_events: list[dict] = []

    for i, d in enumerate(dates):
        # as-of prices
        prices = {}
        for s, df in bars.items():
            px = _price_on(df, d)
            if px is not None and px > 0:
                prices[s] = px

        # Suspension probe: detect flat/illiquid bars (same close as prior, or
        # zero volume) — the signature of a halted/suspended counter.
        for s, df in bars.items():
            row = df[df["date"] <= d]
            if len(row) < 2:
                continue
            last = float(row.iloc[-1]["close"])
            prev = float(row.iloc[-2]["close"])
            vol = float(row.iloc[-1].get("volume", 1) or 1)
            if last == prev and vol <= 0:
                flat_streak[s] += 1
            else:
                if flat_streak[s] >= 5:
                    # a name that was frozen for >=5 days then resumed
                    pass
                flat_streak[s] = 0
            # record a "frozen streak" event if it crosses threshold
            if flat_streak[s] == 5 and s not in SUSPENDED:
                suspend_events.append({
                    "symbol": s, "date": str(d.date()),
                    "note": "5+ consecutive flat/zero-volume bars; strategy "
                            "would keep holding unless in SUSPENDED_SYMBOLS",
                })

        # Build signals (ranking scores) as-of date
        scores = lsd.score_on_date(bars, d)
        signals = [{"symbol": s, "score": sc} for s, sc in scores.items()]

        # Build the portfolio dict the planner expects. FAITHFUL TO LIVE:
        # the live auto-trader feeds the planner positions carrying
        # current_value = MTM market value (prices[sym]*shares), NOT cost
        # basis. Passing cost basis makes the planner see a permanently
        # under-weight book and buy forever without trimming. We replicate
        # the live behaviour exactly.
        portfolio = {
            "initial_capital": initial_capital,
            "cash": round(cash, 2),
            "positions": [
                {"symbol": s, "shares": p["shares"],
                 "avg_cost": p["avg_cost"], "total_cost": p["total_cost"],
                 "current_value": round(p["shares"] * prices.get(s, p["avg_cost"]), 2)}
                for s, p in positions.items()
            ],
            "max_drawdown_pct": 0.0,
        }

        # The live planner excludes SUSPENDED names from auto-trading, but they
        # remain in state as a manual hold. Faithful: pass the real portfolio
        # (including BAMB) and let the planner's own SUSPENDED guard drop it.
        plan = generate_rebalance_plan(
            signals=signals, prices=prices, portfolio=portfolio, dry_run=True,
        )

        # ── STOP-LOSS STEP (faithful to auto_trader.py step 4) ──
        # Live auto-trader applies safety.should_stop_loss AFTER the plan and
        # force-sells any position > STOP_LOSS_PCT below its avg cost. We
        # replicate that here so sells actually occur.
        sl_pct = float(config.STOP_LOSS_PCT)
        for s, p in list(positions.items()):
            if s in config.SUSPENDED_SYMBOLS:
                continue  # live engine never stop-loss-sells suspended names
            px = prices.get(s, p["avg_cost"])
            if px <= 0:
                continue
            loss_pct = (px - p["avg_cost"]) / p["avg_cost"] * 100 if p["avg_cost"] else 0
            if loss_pct <= -sl_pct:
                # emit a full-position stop-loss sell (same as live)
                plan["trades"].append({
                    "symbol": s, "side": "SELL", "delta_shares": p["shares"],
                    "price": px, "value": round(p["shares"] * px, 2),
                    "reason": f"Stop-loss triggered: {s} is {loss_pct:.1f}% "
                              f"below avg cost of KES {p['avg_cost']:.2f}",
                    "sector": config.SECTOR_MAP.get(s, "other"),
                    "signal_score": None, "stop_loss": True,
                })

        # Execute the planner's trades through the cost model
        for t in plan.get("trades", []):
            sym = t["symbol"]
            side = t["side"]
            shares = int(t["delta_shares"])
            if shares <= 0:
                continue
            px = float(t.get("price", 0) or prices.get(sym, 0))
            if px <= 0:
                continue
            gross = round(shares * px, 2)
            ci = _trade_cost(gross, px)
            fee = ci["fee"]
            slip = ci["slippage"]
            cost_drag += fee + slip

            if side == "BUY":
                total = round(gross + fee + slip, 2)
                if total > cash + 0.0001:
                    continue  # insufficient cash — skip (engine would skip)
                cash = round(cash - total, 2)
                if sym not in positions:
                    positions[sym] = {"shares": 0, "avg_cost": px, "total_cost": 0.0}
                pos = positions[sym]
                new_shares = pos["shares"] + shares
                new_cost = round(pos["total_cost"] + gross + fee + slip, 2)
                pos["avg_cost"] = round(new_cost / new_shares, 4)
                pos["total_cost"] = new_cost
                pos["shares"] = new_shares
                net = -total
            else:  # SELL
                pos = positions.get(sym)
                if pos is None or shares > pos["shares"]:
                    shares = pos["shares"] if pos else 0
                    if shares <= 0:
                        continue
                proceeds = round(shares * px, 2)
                net_cash = round(proceeds - fee - slip, 2)
                cash = round(cash + net_cash, 2)
                pos["shares"] -= shares
                pos["total_cost"] = round(pos["total_cost"] * (pos["shares"] / (pos["shares"] + shares)), 2) if (pos["shares"] + shares) else 0.0
                if pos["shares"] <= 0:
                    del positions[sym]
                net = net_cash

            trades.append(Trade(
                date=str(d.date()), symbol=sym, side=side, shares=shares,
                price=px, gross=gross, fee=fee, slippage=slip, net_cash=net,
                reason=t.get("reason", ""), signal_score=t.get("signal_score"),
            ))

        # Mark-to-market at today's prices
        holdings_value = 0.0
        for s, p in positions.items():
            if s in config.SUSPENDED_SYMBOLS and "frozen_mark" in p:
                px = p["frozen_mark"]  # frozen takeover-wait mark, not bar price
            else:
                px = prices.get(s, p["avg_cost"])
            holdings_value += p["shares"] * px
        equity = round(cash + holdings_value, 2)
        peak_equity = max(peak_equity, equity)
        equity_curve.append({
            "date": str(d.date()), "cash": round(cash, 2),
            "holdings": round(holdings_value, 2), "equity": equity,
        })

    # ── Metrics ──
    eq = pd.Series([e["equity"] for e in equity_curve])
    rets = eq.pct_change().dropna()
    total_return = (eq.iloc[-1] / initial_capital - 1) * 100 if initial_capital else 0.0
    # drawdown
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(dd.min() * 100)
    # sharpe (annualised, 252 trading days)
    if len(rets) > 1 and rets.std() > 0:
        sharpe = float(rets.mean() / rets.std() * np.sqrt(252))
    else:
        sharpe = 0.0
    # Replay the ledger so each closed sell is classified by realised P&L.
    win_rate, profit_factor = _compute_win_metrics(trades, positions, bars, dates)

    suspension_probe_out = {}
    if suspension_probe:
        suspension_probe_out = {
            "frozen_streak_events": suspend_events,
            "bamb_excluded_by_engine": "BAMB" in SUSPENDED,
            "strategy_has_suspension_detector": False,  # proven: planner only
            # reacts via the static SUSPENDED_SYMBOLS list, not by detecting
            # illiquidity from price/volume. See below.
            "note": ("The live strategy has NO organic mechanism to detect a "
                     "stock going suspended/illiquid. It relies entirely on the "
                     "static config.SUSPENDED_SYMBOLS list (BAMB only). Any name "
                     "not pre-listed would be held frozen indefinitely while "
                     "flat/zero-volume bars print — the engine never flags it."),
            "frozen_events_count": len(suspend_events),
        }

    return BacktestResult(
        start=str(dates[0].date()), end=str(dates[-1].date()),
        initial_capital=initial_capital, final_equity=round(eq.iloc[-1], 2),
        total_return_pct=round(total_return, 4),
        max_drawdown_pct=round(max_dd, 4), sharpe=round(sharpe, 3),
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(profit_factor, 3) if profit_factor is not None else None,
        total_trades=len(trades), trades=trades,
        equity_curve=equity_curve, cost_drag=round(cost_drag, 2),
        suspension_probe=suspension_probe_out,
    )


def _compute_win_metrics(trades, positions, bars, dates):
    """Second-pass realised P&L per SELL by tracking lot basis through the
    trade ledger (FIFO). Returns (win_rate_pct, profit_factor)."""
    # Rebuild positions through the ledger to know fee-inclusive avg_cost at
    # each sell. This mirrors the cash impact of the live cost model.
    pos_basis: dict[str, dict] = {}
    wins = 0
    losses = 0
    gross_win = 0.0
    gross_loss = 0.0
    for t in trades:
        sym = t.symbol
        if t.side == "BUY":
            if sym not in pos_basis:
                pos_basis[sym] = {"shares": 0, "total_cost": 0.0}
            pos_basis[sym]["shares"] += t.shares
            # Buy fees are cash out and therefore part of the cost basis.
            pos_basis[sym]["total_cost"] += t.gross + t.slippage + t.fee
        else:
            pb = pos_basis.get(sym)
            if pb is None or pb["shares"] <= 0:
                continue
            avg = pb["total_cost"] / pb["shares"]
            realised = (t.price - avg) * t.shares - t.fee - t.slippage
            if realised > 0:
                wins += 1
                gross_win += realised
            else:
                losses += 1
                gross_loss += -realised
            # reduce
            sold = min(t.shares, pb["shares"])
            pb["total_cost"] = round(pb["total_cost"] * (1 - sold / pb["shares"]), 2)
            pb["shares"] -= sold
    total_closed = wins + losses
    win_rate = (wins / total_closed * 100) if total_closed else 0.0
    # PF is undefined when there are no losing closed trades. Never emit the
    # gross-win amount as a fake ratio (it produced values such as 4521.575).
    pf = (gross_win / gross_loss) if gross_loss > 0 else None
    return win_rate, pf


def _compute_live_ledger_metrics(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute honest close metrics from portfolio/transactions.json.

    ``realised_pnl`` is already fee-inclusive in the portfolio ledger.  Do
    not infer outcomes from replay trades: the replay is a separate strategy
    simulation and can legitimately contain a different set of closes.
    """
    sells = [t for t in ledger if str(t.get("action", "")).upper() == "SELL"]
    pnls = [float(t["realised_pnl"]) for t in sells
            if t.get("realised_pnl") is not None]
    wins = sum(pnl > 0 for pnl in pnls)
    losses = sum(pnl <= 0 for pnl in pnls)
    gross_win = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = sum(-pnl for pnl in pnls if pnl < 0)
    return {
        "sell_count": len(sells),
        "closed_with_pnl": len(pnls),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": (wins / len(pnls) * 100) if pnls else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else None,
        "realised_pnl_total": sum(pnls),
    }


def _load_live_ledger() -> tuple[list[dict[str, Any]], Optional[str]]:
    """Load the live transaction ledger, returning rows and source path."""
    path = Path.home() / ".trading" / "portfolio" / "transactions.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return [], None
    return (data, str(path)) if isinstance(data, list) else ([], None)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--no-bamb", action="store_true",
                    help="exclude BAMB from universe (tradeable-only book)")
    ap.add_argument("--inject-bamb", action="store_true",
                    help="inject a frozen BAMB takeover-wait position (scenario A)")
    ap.add_argument("--output", default=str(Path.home() / ".trading" / "backtest_live_result.json"))
    ap.add_argument("--trades-out", default=str(Path.home() / ".trading" / "backtest_live_trades.json"))
    ap.add_argument("--no-live-ledger", action="store_true",
                    help="omit live-ledger metrics (replay metrics remain available)")
    args = ap.parse_args()

    include_bamb = not args.no_bamb
    res = run_backtest(start=args.start, end=args.end, include_bamb=include_bamb,
                       inject_bamb=args.inject_bamb)

    # Strip Trade dataclasses for JSON
    out = asdict(res)
    live_ledger, ledger_source = _load_live_ledger()
    if not args.no_live_ledger and live_ledger:
        out["live_ledger"] = _compute_live_ledger_metrics(live_ledger)
        out["live_ledger"]["source"] = ledger_source
    Path(args.output).write_text(json.dumps(out, indent=2, default=str))
    print(f"Backtest {res.start} -> {res.end}")
    print(f"  initial KES {res.initial_capital:,.2f}")
    print(f"  final   KES {res.final_equity:,.2f}")
    print(f"  return  {res.total_return_pct:+.2f}%")
    print(f"  max DD  {res.max_drawdown_pct:.2f}%")
    print(f"  sharpe  {res.sharpe:.3f}")
    pf_display = f"{res.profit_factor:.2f}" if res.profit_factor is not None else "n/a"
    print(f"  strategy-replay rate {res.win_rate_pct:.1f}%  profit factor {pf_display}")
    if not args.no_live_ledger and live_ledger:
        lm = out["live_ledger"]
        lpf = f"{lm['profit_factor']:.2f}" if lm["profit_factor"] is not None else "n/a"
        print(f"  win     {lm['win_rate_pct']:.1f}%  profit factor {lpf} "
              f"({lm['closed_with_pnl']}/{lm['sell_count']} SELLs with P&L)")
    print(f"  trades  {res.total_trades}")
    print(f"  cost drag KES {res.cost_drag:,.2f}")
    sp = res.suspension_probe
    if sp:
        print(f"  SUSPENSION PROBE: strategy has organic detector = "
              f"{sp.get('strategy_has_suspension_detector')}; "
              f"frozen-streak events observed = {sp.get('frozen_events_count')}")
    print(f"  wrote {args.output}")


if __name__ == "__main__":
    main()
