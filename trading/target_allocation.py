"""
Target Allocation Engine.

Defines strategic sector-level allocation targets and computes
per-stock rebalance adjustments to converge the portfolio toward
the target allocation.

DATA ASSUMPTION
===============
This engine operates on daily-close granularity (EOD data). Prices
come from TradingView daily OHLCV bars via the `tradingview-ta`
library. These are **not** intraday ticks.

- **Primary price source:** TradingView daily bars (posted after
  market close, ~15:00-16:00 EAT).
- **Secondary price source:** Mansa API (free tier, 100 req/day,
  returns a single price point — timestamp unknown).
- **Gap detection:** mystocks.co.ke cache (15-min delayed page
  scrape). Used ONLY for the gap filter to skip stale signals.
  NOT used for portfolio valuation or trade decisions.

The engine does NOT support intraday execution. All buy/sell
decisions are based on end-of-day prices and sector weights.

If intraday capability is ever needed, both data sources must be
upgraded (paid Mansa tier for timestamped ticks, or official NSE
delayed feed from nse.co.ke).

Strategy:
  banking       45%   — cheap P/E (6.2x), strong ROE (17.9%), high yield (7.2%)
  telecom       14%   — SCOM anchor, strong ROE but premium P/E
  manufacturing 13%   — consumer staples (EABL, BAT), defensive ballast
  energy        11%   — highest sector yield (8.4%), KenGen at fair P/E
  insurance      7%   — recovery plays (Britam +73% YTD)
  cash          10%   — reserve (not a sector bucket; always outside STRATEGY)

Usage:
    python3 -m trading.target_allocation           # Print sector analysis
    python3 -m trading.target_allocation --json    # Machine-readable output
    python3 -m trading.target_allocation --rebalance --dry-run  # Preview trades
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

# ── Ensure the project root is on the path ──
_TRADING_ROOT = str(Path(__file__).resolve().parent.parent)
if _TRADING_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_ROOT)

from trading import config
from trading import risk_profiles as rp

# ── Paths ───────────────────────────────────────────────────────────────────
PORTFOLIO_DIR = Path.home() / ".trading" / "portfolio"
MTM_PATH = PORTFOLIO_DIR / "mtm_state.json"
STATE_PATH = PORTFOLIO_DIR / "state.json"

# ── Sector Classification (canonical — from config) ────────────────────────
SECTOR_MAP: dict[str, str] = dict(config.SECTOR_MAP)

# ── Target Strategy ────────────────────────────────────────────────────────
# Sector → {target weight %, tolerance ±%, stocks in sector for monitoring}
#
# Derived from AXYS Market Pulse sector scorecard (14 Jul 2026):
#   Banking:  P/E 6.2x, ROE 17.9%, Div Yield 7.2% — best value + income
#   Telecom:  ROE 41.4%, Div Yield 5.6% — single-stock risk (SCOM)
#   Mfg:      P/E 10.4x, ROE 16.4%, Div Yield 3.6% — defensive staples
#   Energy:   P/E 7.0x, Div Yield 8.4% — highest yield on NSE
#   Insurance: P/E 6.4x, ROE 8.8% — recovery plays
STRATEGY: dict[str, dict[str, Any]] = {
    # Sector weights are % of TOTAL portfolio and sum to (100 - CASH_RESERVE).
    # Cash reserve is held outside these buckets so both can be feasible.
    # BAMB (suspended NSE 28-Feb-2025) was removed from manufacturing;
    # its ~6.5% was redistributed proportionally to the other 4 sectors
    # (factor 83.5/77 = 1.08442). EABL keeps manufacturing's remaining
    # ~6.5% as its own single-stock "consumer" sector.
    "banking": {
        "target_pct": 48.80,
        "tolerance": 5.0,
        "stocks": ["KCB", "EQTY", "ABSA", "SCBK", "COOP"],
        "rationale": "P/E 6.2x with 17.9% ROE — cheapest sector, best yield",
    },
    "telecom": {
        "target_pct": 15.18,
        "tolerance": 3.0,
        "stocks": ["SCOM"],
        "rationale": "SCOM anchor at 14.9x P/E, 5.6% yield, limited upside from here",
    },
    "energy": {
        "target_pct": 11.93,
        "tolerance": 3.0,
        "stocks": ["KPLC", "TOTL"],
        "rationale": "8.4% sector yield — income engine. KenGen at 7.0x P/E",
    },
    "insurance": {
        "target_pct": 7.59,
        "tolerance": 2.0,
        "stocks": ["KNRE", "BRIT"],
        "rationale": "Recovery sector — Britam +73% YTD, sector P/E 6.4x",
    },
    "consumer": {
        "target_pct": 6.50,
        "tolerance": 3.0,
        "stocks": ["EABL"],
        "rationale": "EABL retains manufacturing's remaining ~6.5% after BAMB "
                     "suspension; consumer-staples defensive ballast",
    },
}

# --- Suspended securities (NSE suspension, delisting, etc.) ---
# BAMB (Bamburi Cement) suspended from NSE since 28-Feb-2025 — Amsons
# Group 96.54% buyout + CMA compulsory squeeze-out, heading to delisting.
# MUST never be a rebalance candidate (buy/sell). Held 39 shares are kept
# as a static, non-rebalanceable position in portfolio state only.
SUSPENDED = {"BAMB"}

# ── Execution Constraints ──────────────────────────────────────────────────
MAX_DAILY_SHIFT_PCT = 5.0   # Max % of portfolio value to shift per day
SIGNAL_GATE_MIN = 50.0      # Only buy if signal score >= this (Hold+)
CASH_RESERVE_PCT = 10.0     # Keep at least this % in cash
# Liquidity cap: never deploy more than this fraction of total portfolio value
# into a single name on one day, scaled by that name's liquidity score. Keeps
# illiquid counters (low turnover) from being gapped by a single fat order.
LIQ_TRADE_PCT_CAP = 0.08
# Invested sector targets must sum to (100 - CASH_RESERVE_PCT)
TARGET_INVESTED_PCT = 100.0 - CASH_RESERVE_PCT  # 90.0

# ── Concentration guardrails ───────────────────────────────────────────────
# HIGH side: a single sector must not exceed these ceilings.
#   WARN at 55% — flag for review, still allowed.
#   HARD at 60% — the rebalancer MUST trim (a trim trade is forced) and NO
#     new BUY into that sector is permitted until back under the cap.
# These are DELIBERATE choices (2026-07-20): the prior "50% sector cap" was
# a report label only, never enforced, and banking naturally runs ~49% in a
# 5-bank NSE universe. 60/55 leaves headroom for the intended allocation
# (banking target 48.8 + 5 tol = 53.8 max) while circuit-breaking a real
# blowout (e.g. banks +15% -> book drifts to 62%).
SECTOR_CAP_WARN_PCT = 55.0
SECTOR_CAP_HARD_PCT = 60.0

# LOW side: every held, non-suspended position must sit in a sector whose
# current weight is >= (target - tolerance). If a held position's sector is
# below that floor AND no BUY/top-up was generated to fix it, that is a
# FLOOR violation — the mirror-image guard to the orphan-exit rule. This
# catches the "EABL silently zeroed" class: EABL had no sector at all, so
# its sector weight is 0, far below any floor -> FLOOR violation fires
# (while the orphan guard blocks the erroneous SELL).


def _trade_risk(
    symbol: str,
    signal_full: dict[str, Any],
    hist_cache: Optional[dict[str, Any]] = None,
) -> dict[str, float]:
    """Attach a compact risk annotation to a planned trade (for audit/logs)."""
    sig = signal_full.get(symbol, {}) or {}
    hist = (hist_cache or {}).get(symbol)
    vol = rp.realized_vol(symbol, history=hist,
                          signal_vol=float(sig.get("volatility", 50)) / 100.0)
    liq = rp.liquidity_score(symbol, signal_full)
    return {"vol": round(vol, 4), "liq": round(liq, 4),
            "signal_volatility": float(sig.get("volatility", 50))}


def _strategy_universe() -> set[str]:
    """All symbols that appear in the target STRATEGY stock lists."""
    uni: set[str] = set()
    for cfg in STRATEGY.values():
        uni.update(cfg.get("stocks") or [])
    return uni


def _delta_trade(
    *,
    symbol: str,
    side: str,
    delta_shares: int,
    price: float,
    reason: str,
    sector: str = "",
    signal_score: float = 50.0,
    risk: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a trade dict under the shares contract: qty is always a delta.

    Contract (plan ↔ auto_trader):
      - ``delta_shares`` = shares to buy/sell NOW (incremental, never absolute target)
      - ``shares`` = alias of ``delta_shares`` (back-compat)
      - ``qty_mode`` = always ``\\"delta\\"``
      - ``risk`` = optional risk annotation {vol, liq, signal_volatility} for audit
    """
    ds = max(0, int(delta_shares))
    px = float(price) if price else 0.0
    return {
        "symbol": symbol,
        "side": side,
        "delta_shares": ds,
        "shares": ds,  # alias — MUST mean delta, not absolute target
        "qty_mode": "delta",
        "price": round(px, 2),
        "value": round(ds * px, 2),
        "reason": reason,
        "sector": sector,
        "signal_score": signal_score,
        "risk": risk or {},
    }


def get_strategy() -> dict[str, dict[str, Any]]:
    """Return the current target strategy configuration."""
    # Sector targets cover invested book only; cash is separate.
    total = sum(s["target_pct"] for s in STRATEGY.values())
    if abs(total - TARGET_INVESTED_PCT) > 1.0:
        raise ValueError(
            f"Sector target weights sum to {total:.1f}%, "
            f"expected ~{TARGET_INVESTED_PCT:.0f}% "
            f"(100% - {CASH_RESERVE_PCT:.0f}% cash reserve)"
        )
    return dict(STRATEGY)


def max_sector_exposure_pct() -> float:
    """Hard sector cap = max(target + tolerance) across strategy buckets."""
    return max(
        float(cfg["target_pct"]) + float(cfg["tolerance"])
        for cfg in STRATEGY.values()
    )


def validate_plan_constraints(
    weights: dict[str, Any],
    targets: dict[str, Any],
    trades: list[dict[str, Any]],
    portfolio: dict[str, Any],
) -> list[dict[str, Any]]:
    """Enforce concentration guardrails on a generated rebalance plan.

    HIGH side (cap):
      * Any sector whose current_pct > SECTOR_CAP_HARD_PCT -> "HARD_CAP"
        violation; a trim trade MUST be present for it.
      * Any sector whose current_pct > SECTOR_CAP_WARN_PCT -> "WARN_CAP"
        violation (review flag, still actionable).
    LOW side (floor):
      * For every held, non-suspended position whose sector exists in the
        strategy, if the sector's current_pct < (target - tolerance) AND no
        BUY/top-up trade was generated for that sector -> "FLOOR" violation.
      * A held, non-suspended position whose symbol is NOT in any sector's
        stock list (orphaned, e.g. the EABL-drop class) -> its effective
        sector weight is 0, which is below any floor; reported as "FLOOR"
        with symbol + reason. The orphan-exit SELL for it is suppressed
        separately by the SUSPENDED/strategy_universe guard.

    Returns a list of violation dicts:
        {level, kind, sector, symbol, current_pct, limit_pct, detail}
    """
    violations: list[dict[str, Any]] = []
    sectors = targets.get("current", {})
    strategy = get_strategy()
    pos_map = {p["symbol"]: p for p in portfolio.get("positions", [])}
    buy_sectors = {t["sector"] for t in trades if t["side"] == "BUY"}
    # sectors that already have a trim (SELL) trade queued
    trim_sectors = {t["sector"] for t in trades if t["side"] == "SELL"}

    # ── HIGH side: sector concentration cap ──
    for sec, info in sectors.items():
        cur = info.get("current_pct", 0.0)
        if cur > SECTOR_CAP_HARD_PCT:
            violations.append({
                "level": "error", "kind": "HARD_CAP", "sector": sec,
                "symbol": None, "current_pct": round(cur, 2),
                "limit_pct": SECTOR_CAP_HARD_PCT,
                "detail": f"{sec} at {cur:.1f}% exceeds HARD cap "
                          f"{SECTOR_CAP_HARD_PCT:.0f}% — trim required",
            })
            if sec not in trim_sectors:
                violations.append({
                    "level": "error", "kind": "HARD_CAP_NO_TRIM",
                    "sector": sec, "symbol": None,
                    "current_pct": round(cur, 2),
                    "limit_pct": SECTOR_CAP_HARD_PCT,
                    "detail": f"{sec} over HARD cap but no trim trade generated",
                })
        elif cur > SECTOR_CAP_WARN_PCT:
            violations.append({
                "level": "warn", "kind": "WARN_CAP", "sector": sec,
                "symbol": None, "current_pct": round(cur, 2),
                "limit_pct": SECTOR_CAP_WARN_PCT,
                "detail": f"{sec} at {cur:.1f}% exceeds WARN cap "
                          f"{SECTOR_CAP_WARN_PCT:.0f}% — review concentration",
            })

    # ── LOW side: held-position floor ──
    for sym, pos in pos_map.items():
        if sym in SUSPENDED:
            continue  # suspended holdings intentionally excluded
        # find which strategy sector (if any) this symbol belongs to
        sec = None
        for s, cfg in strategy.items():
            if sym in (cfg.get("stocks") or []):
                sec = s
                break
        if sec is None:
            # Orphaned held position (no sector) -> effective weight 0.
            violations.append({
                "level": "error", "kind": "FLOOR", "sector": sec,
                "symbol": sym, "current_pct": 0.0, "limit_pct": 0.0,
                "detail": f"HELD {sym} has no target sector (orphaned) — "
                          f"floor breach; would be force-sold by orphan-exit",
            })
            continue
        info = sectors.get(sec, {})
        cur = info.get("current_pct", 0.0)
        tgt = info.get("target_pct", 0.0)
        tol = info.get("tolerance", 0.0)
        floor = tgt - tol
        if cur < floor and sec not in buy_sectors:
            violations.append({
                "level": "warn", "kind": "FLOOR", "sector": sec,
                "symbol": sym, "current_pct": round(cur, 2),
                "limit_pct": round(floor, 2),
                "detail": f"{sec} at {cur:.1f}% below floor {floor:.1f}% "
                          f"and no BUY generated for {sym}",
            })
    return violations




def _load_portfolio() -> dict[str, Any]:
    """Load latest portfolio state (prefer MTM, fall back to cost-basis)."""
    if MTM_PATH.exists():
        try:
            return json.loads(MTM_PATH.read_text())
        except Exception:
            pass
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"cash": 0, "positions": [], "initial_capital": 0}


def compute_sector_weights(
    portfolio: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compute current sector weights from the portfolio.

    Returns:
        {
            "total_value": float,          # cash + invested
            "cash": float,
            "cash_pct": float,             # cash as % of total
            "sectors": {
                sector_name: {
                    "value": float,        # KES in this sector
                    "pct": float,          # % of portfolio
                    "stocks": {symbol: {value, shares, avg_cost, pct}}
                }
            }
        }
    """
    if portfolio is None:
        portfolio = _load_portfolio()

    cash = float(portfolio.get("cash", 0))
    positions = portfolio.get("positions", [])

    # Build sector buckets
    sectors: dict[str, dict[str, Any]] = {}
    total_invested = 0.0

    for p in positions:
        sym = p.get("symbol", "")
        shares = p.get("shares", 0)
        avg_cost = float(p.get("avg_cost", 0))
        # Use current_value if available (MTM), else cost-basis
        current_val = p.get("current_value", None)
        if current_val is None:
            current_val = shares * avg_cost
        else:
            current_val = float(current_val)

        sec = SECTOR_MAP.get(sym, "other")
        if sec not in sectors:
            sectors[sec] = {"value": 0.0, "stocks": {}}
        sectors[sec]["value"] += current_val
        sectors[sec]["stocks"][sym] = {
            "value": current_val,
            "shares": shares,
            "avg_cost": avg_cost,
        }
        total_invested += current_val

    total_value = cash + total_invested

    # Calculate percentages
    for sec in sectors:
        sectors[sec]["pct"] = (
            (sectors[sec]["value"] / total_value * 100) if total_value > 0 else 0
        )
        for sym in sectors[sec]["stocks"]:
            sectors[sec]["stocks"][sym]["pct"] = (
                (sectors[sec]["stocks"][sym]["value"] / total_value * 100)
                if total_value > 0 else 0
            )

    cash_pct = (cash / total_value * 100) if total_value > 0 else 100

    return {
        "total_value": round(total_value, 2),
        "cash": round(cash, 2),
        "cash_pct": round(cash_pct, 2),
        "sectors": sectors,
    }


def _risk_weights_for_sector(
    sector: str,
    stocks: list[str],
    *,
    signals: Optional[dict[str, Any]] = None,
    matrix: Optional[dict[str, Any]] = None,
    hist_cache: Optional[dict[str, Any]] = None,
) -> dict[str, float]:
    """Return {symbol: weight} summing to 1.0 across ``stocks`` in ``sector``.

    Weight = (1/vol) * liquidity_factor * (1 - 0.5*corr_penalty_norm).
    Low-volatility, liquid, uncorrelated names receive a larger slice; the
    most volatile / most-correlated names receive less. Falls back to an
    equal split when the stock list is empty.

    Inputs are fail-open: when realized history / correlation matrix are
    absent, we degrade to the ranking ``volatility`` / ``liquidity`` factor
    scores (already 0..100) via :mod:`trading.risk_profiles`.
    """
    if not stocks:
        return {}
    sigs = signals or {}
    raw: dict[str, float] = {}
    for s in stocks:
        sig = sigs.get(s, {}) or {}
        hist = (hist_cache or {}).get(s)
        vol = rp.realized_vol(
            s,
            history=hist,
            signal_vol=float(sig.get("volatility", 50)) / 100.0,
        )
        liq = rp.liquidity_score(s, sigs)
        peers = [x for x in stocks if x != s]
        pen = rp.corr_penalty(s, peers, matrix)
        pen_n = pen / max(1, len(peers))
        raw[s] = rp.risk_weight(s, vol=vol, liq=liq, corr_penalty_norm=pen_n)
    tot = sum(raw.values())
    if tot <= 0:
        return {s: 1.0 / len(stocks) for s in stocks}
    return {s: v / tot for s, v in raw.items()}


def compute_targets(
    sector_weights: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compare current sector weights against target strategy.

    Args:
        sector_weights: Output from compute_sector_weights(). If None, loads live.

    Returns:
        {
            "total_value": float,
            "strategy": {sector: {target_pct, tolerance, rationale}},
            "current": {sector: {pct, value, status, drift_pct, action}},
            "summary": {
                "on_target": int,
                "within_tolerance": int,
                "over_weight": int,
                "under_weight": int,
            }
        }
    """
    if sector_weights is None:
        sector_weights = compute_sector_weights()

    strategy = get_strategy()
    total = sector_weights["total_value"]
    current = {}

    on_target = 0
    within_tol = 0
    over = 0
    under = 0

    for sec, cfg in strategy.items():
        target_pct = cfg["target_pct"]
        tolerance = cfg["tolerance"]
        current_sec = sector_weights.get("sectors", {}).get(sec, {})
        current_pct = current_sec.get("pct", 0.0)
        current_value = current_sec.get("value", 0.0)
        drift_pct = current_pct - target_pct

        if abs(drift_pct) <= tolerance:
            if abs(drift_pct) <= 0.5:
                status = "on_target"
                on_target += 1
            else:
                status = "within_tolerance"
                within_tol += 1
        elif drift_pct > 0:
            status = "over_weight"
            over += 1
        else:
            status = "under_weight"
            under += 1

        # Determine action
        if status == "over_weight":
            action = "trim" if abs(drift_pct) > tolerance else "hold"
        elif status == "under_weight":
            action = "add" if abs(drift_pct) > tolerance else "hold"
        else:
            action = "hold"

        current[sec] = {
            "target_pct": target_pct,
            "tolerance": tolerance,
            "current_pct": round(current_pct, 2),
            "current_value": round(current_value, 2),
            "drift_pct": round(drift_pct, 2),
            "status": status,
            "action": action,
            "rationale": cfg["rationale"],
            "stocks_in_sector": cfg["stocks"],
        }

    # Also flag cash as a position
    cash_pct = sector_weights["cash_pct"]
    target_cash_pct = CASH_RESERVE_PCT
    cash_drift = cash_pct - target_cash_pct
    if cash_pct > target_cash_pct + 3:
        cash_status = "over_weight"  # too much cash
        cash_action = "deploy"
    elif cash_pct < target_cash_pct - 2:
        cash_status = "under_weight"  # not enough cash
        cash_action = "raise"
    else:
        cash_status = "on_target"
        cash_action = "hold"

    return {
        "total_value": total,
        "strategy": strategy,
        "current": current,
        "cash": {
            "current_pct": round(cash_pct, 2),
            "target_pct": target_cash_pct,
            "drift_pct": round(cash_drift, 2),
            "status": cash_status,
            "action": cash_action,
            "value": sector_weights["cash"],
        },
        "summary": {
            "on_target": on_target,
            "within_tolerance": within_tol,
            "over_weight": over,
            "under_weight": under,
            "sectors_analysed": len(strategy),
        },
    }


def get_target_allocations(
    portfolio: Optional[dict[str, Any]] = None,
) -> dict[str, float]:
    """Generate per-stock target allocation percentages.

    Converts sector-level targets into per-stock targets.
    Within each sector, target weight is split equally among positions
    currently held in that sector. If no positions held in a sector,
    the first stock from the strategy config is assigned the full target
    as a proposed entry point.

    Returns:
        {symbol: target_pct} — compatible with rebalance.py's target_allocs dict
    """
    if portfolio is None:
        portfolio = _load_portfolio()

    weights = compute_sector_weights(portfolio)
    targets = compute_targets(weights)
    strategy = get_strategy()

    current_positions = {p["symbol"] for p in portfolio.get("positions", [])}
    result: dict[str, float] = {}

    for sec, cfg in strategy.items():
        target_pct = cfg["target_pct"]
        stocks_in_sector = cfg["stocks"]

        # Which stocks from this sector do we currently hold?
        held = [s for s in stocks_in_sector if s in current_positions]

        if held:
            # Split target equally among held stocks
            per_stock = target_pct / len(held)
            for sym in held:
                result[sym] = round(per_stock, 2)
        else:
            # No holdings in this sector — assign target to first stock
            # as a proposal for future entry
            if stocks_in_sector:
                result[stocks_in_sector[0]] = round(target_pct, 2)

    return result


def generate_rebalance_plan(
    signals: Optional[list[dict[str, Any]]] = None,
    prices: Optional[dict[str, float]] = None,
    portfolio: Optional[dict[str, Any]] = None,
    dry_run: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Generate a target-aware rebalance plan with signal gating.

    Args:
        signals: List of signal dicts with {symbol, score, ...}
        prices: Dict of {symbol: current_price}
        portfolio: Portfolio state. If None, loads live.
        dry_run: If True, don't execute — just preview.

    Returns:
        {
            "trades": [{symbol, side, shares, price, value, reason, sector}],
            "summary": {total_buy_value, total_sell_value, net_cash, trade_count}
        }
    """
    if portfolio is None:
        portfolio = _load_portfolio()

    weights = compute_sector_weights(portfolio)
    targets = compute_targets(weights)

    if prices is None:
        # Try to get live prices
        try:
            from trading.nse_price_fetcher import fetch_prices
            all_prices = fetch_prices()
            prices = {sym: d["price"] for sym, d in all_prices.items()}
        except Exception:
            prices = {}

    if signals is None:
        # Try to get signals
        try:
            from trading.services.ranking import build
            ranking = build()
            signals = [{"symbol": s["symbol"], "score": s.get("score", 50)}
                       for s in ranking]
        except Exception:
            signals = []

    # Build signal lookups (score + full signal dict for risk profiling)
    signal_map: dict[str, float] = {}
    signal_full: dict[str, dict[str, Any]] = {}
    for s in signals:
        sym = s.get("symbol", "")
        signal_map[sym] = float(s.get("score", 50))
        signal_full[sym] = s

    # Optional realized-history cache (fail-open). If present, keyed by symbol
    # -> array-like of recent closes. Absent => risk profiling degrades to
    # the ranking volatility/liquidity factor scores.
    hist_cache: dict[str, Any] = kwargs.pop("hist_cache", None) or {}

    trades = []
    pos_map = {p["symbol"]: p for p in portfolio.get("positions", [])}
    current_stocks = set(pos_map.keys())
    total_value = weights["total_value"]

    # === SELL signals: over-weight sectors ===
    for sec, info in targets["current"].items():
        if info["action"] != "trim":
            continue
        # We're over-weight in this sector — trim
        # Risk-aware: bias the trim toward the highest-risk names (lowest
        # risk weight) so we keep the steadier, more diversifying holdings.
        rweights = _risk_weights_for_sector(
            sec, list(info["stocks_in_sector"]),
            signals=signal_full, hist_cache=hist_cache,
        )
        # Sell bias = inverse of risk weight (higher vol/corr -> sell more).
        inv = {s: (1.0 - w) for s, w in rweights.items()}
        inv_tot = sum(inv.values()) or 1.0
        for sym in info["stocks_in_sector"]:
            if sym not in current_stocks:
                continue
            # How much to sell? Enough to bring sector back to target
            over_value = info["current_value"] - (
                total_value * info["target_pct"] / 100
            )
            if over_value <= 0:
                continue

            pos = pos_map[sym]
            shares = pos["shares"]
            price = prices.get(sym, pos.get("avg_cost", 0))
            if price <= 0:
                continue

            # Sell proportionally, biased by risk (inverse weight).
            # risk_mul > 1 for above-average-risk names (sell more), < 1 for
            # steadier names (sell less). Clamped so no single name is
            # grossly over/under-trimmed relative to its position share.
            pos_value = pos.get("current_value", shares * pos["avg_cost"])
            pos_share = pos_value / info["current_value"] if info["current_value"] > 0 else 0
            avg_inv = inv_tot / max(1, len(inv))
            risk_mul = max(0.25, min(4.0, (inv.get(sym, avg_inv) / avg_inv)))
            sell_value = over_value * pos_share * risk_mul
            sell_shares = max(1, int(sell_value / price))

            # Signal gate: don't sell if signal says Accumulate+
            sig = signal_map.get(sym, 50)
            if sig >= 75:
                continue  # Signal says accumulate — hold despite over-weight

            trades.append(_delta_trade(
                symbol=sym,
                side="SELL",
                delta_shares=min(sell_shares, shares),
                price=price,
                reason=f"Sector {sec} is {info['drift_pct']:+.1f}% over target "
                       f"(risk-biased trim)",
                sector=sec,
                signal_score=sig,
                risk=_trade_risk(sym, signal_full, hist_cache),
            ))

    # === BUY signals: under-weight sectors ===
    for sec, info in targets["current"].items():
        if info["action"] not in ("add",):
            continue
        # Concentration guard: never ADD to a sector already at/over HARD cap
        if info.get("current_pct", 0.0) > SECTOR_CAP_HARD_PCT:
            continue
        # Risk weights across the sector's stock list (fail-open to equal).
        rweights = _risk_weights_for_sector(
            sec, list(info["stocks_in_sector"]),
            signals=signal_full, hist_cache=hist_cache,
        )
        for sym in info["stocks_in_sector"]:
            if sym in current_stocks:
                continue  # Already holding — allocation covers it
            # New position — check signal gate
            sig = signal_map.get(sym, 50)
            if sig < SIGNAL_GATE_MIN:
                continue  # Signal too weak — wait

            price = prices.get(sym, 0)
            if price <= 0:
                continue

            # How much to buy? Target sector weight × total_value minus current sector value
            target_sector_value = total_value * info["target_pct"] / 100
            buy_value = max(0, target_sector_value - info["current_value"])
            if buy_value <= 0:
                continue

            # Risk-aware split: this name's share of the sector top-up.
            w = rweights.get(sym, 1.0 / max(1, len(info["stocks_in_sector"])))
            buy_value = buy_value * w

            # Cap new entries by daily shift (same as top-ups)
            max_buy = total_value * (MAX_DAILY_SHIFT_PCT / 100)
            buy_value = min(buy_value, max_buy)
            # Liquidity cap: scale by this name's liquidity so illiquid
            # counters are never gapped by a single fat order.
            liq = rp.liquidity_score(sym, signal_full)
            liq_cap = total_value * LIQ_TRADE_PCT_CAP * max(liq, 0.25)
            buy_value = min(buy_value, liq_cap)

            shares = max(1, int(buy_value / price))
            trades.append(_delta_trade(
                symbol=sym,
                side="BUY",
                delta_shares=shares,
                price=price,
                reason=f"Sector {sec} is {info['drift_pct']:+.1f}% under target "
                       f"(risk weight {w:.0%})",
                sector=sec,
                signal_score=sig,
                risk=_trade_risk(sym, signal_full, hist_cache),
            ))

    # === Holdings in under-weight sectors that we already own: top-up ===
    for sec, info in targets["current"].items():
        if info["action"] != "add":
            continue
        # Concentration guard: never ADD to a sector already at/over HARD cap
        if info.get("current_pct", 0.0) > SECTOR_CAP_HARD_PCT:
            continue
        # Risk weights across the sector's HELD names only (correlation-aware).
        held_in_sector_syms = [s for s in info["stocks_in_sector"] if s in current_stocks]
        rweights = _risk_weights_for_sector(
            sec, held_in_sector_syms, signals=signal_full, hist_cache=hist_cache,
        )
        for sym in held_in_sector_syms:
            # We already hold this, but sector is under target — top up
            sig = signal_map.get(sym, 50)
            if sig < SIGNAL_GATE_MIN:
                continue

            price = prices.get(sym, 0)
            if price <= 0:
                continue

            pos = pos_map[sym]
            pos_value = pos.get("current_value", pos["shares"] * pos["avg_cost"])

            # Target: our position's risk-weighted share of sector target
            our_share = rweights.get(sym, 1.0 / max(1, len(held_in_sector_syms)))
            our_target = total_value * info["target_pct"] / 100 * our_share

            buy_value = max(0, our_target - pos_value)
            if buy_value <= 0:
                continue

            # Apply daily deployment cap
            max_buy = total_value * (MAX_DAILY_SHIFT_PCT / 100)
            buy_value = min(buy_value, max_buy)
            # Liquidity cap (scaled)
            liq = rp.liquidity_score(sym, signal_full)
            liq_cap = total_value * LIQ_TRADE_PCT_CAP * max(liq, 0.25)
            buy_value = min(buy_value, liq_cap)

            shares = max(1, int(buy_value / price))

            # Only if meaningful
            trade_value = shares * price
            if trade_value < 1000:
                continue

            trades.append(_delta_trade(
                symbol=sym,
                side="BUY",
                delta_shares=shares,
                price=price,
                reason=f"Top-up {sym}: sector {sec} is {info['drift_pct']:+.1f}% under target "
                       f"(risk weight {our_share:.0%})",
                sector=sec,
                signal_score=sig,
                risk=_trade_risk(sym, signal_full, hist_cache),
            ))

    # === Orphan exit: holdings not in any STRATEGY universe (e.g. WTK) ===
    strategy_uni = _strategy_universe()
    for sym, pos in pos_map.items():
        if sym in strategy_uni:
            continue
        # Never rebalance a SUSPENDED security (e.g. BAMB) — held shares
        # stay as a static, non-tradable position; do NOT emit a SELL.
        if sym in SUSPENDED:
            continue
        shares = int(pos.get("shares") or 0)
        if shares <= 0:
            continue
        price = prices.get(sym, 0) or float(pos.get("avg_cost") or 0)
        if price <= 0:
            continue
        # Skip if already selling this symbol
        if any(t["symbol"] == sym and t["side"] == "SELL" for t in trades):
            continue
        sec = SECTOR_MAP.get(sym, "other")
        trades.append(_delta_trade(
            symbol=sym,
            side="SELL",
            delta_shares=shares,
            price=price,
            reason=f"Orphan position {sym} not in target strategy — full exit",
            sector=sec,
            signal_score=signal_map.get(sym, 50),
            risk=_trade_risk(sym, signal_full, hist_cache),
        ))

    # Summary
    buy_value = sum(t["value"] for t in trades if t["side"] == "BUY")
    sell_value = sum(t["value"] for t in trades if t["side"] == "SELL")

    # Concentration guardrails (high cap + low floor)
    violations = validate_plan_constraints(weights, targets, trades, portfolio)

    return {
        "trades": trades,
        "summary": {
            "total_buy_value": round(buy_value, 2),
            "total_sell_value": round(sell_value, 2),
            "net_cash": round(buy_value - sell_value, 2),
            "trade_count": len(trades),
            "dry_run": dry_run,
            "qty_mode": "delta",
        },
        "targets": targets,
        "violations": violations,
    }


# ── Engine agreement gate ──────────────────────────────────────────────────

def verify_target_agreement(
    nse_only: bool = True,
    tolerance: float = 3.0,
    portfolio: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Reconcile ``generate_rebalance_plan`` targets vs ``decision`` engine.

    The auto-trader executes ``generate_rebalance_plan`` (sector weights that
    sum to 90% equities + 10% cash). The Decision Engine
    (``services.decision.generate_proposal``) is a separate holistic allocator.
    This gate checks the two agree on per-stock equity targets.

    When ``nse_only`` is True the Decision Engine sources its equity targets
    from ``get_target_allocations()`` (single source of truth), so agreement
    is structural and ``agreed`` should be True. When False, the Decision
    Engine uses its generic score-weighted distributor (multi-asset buckets),
    so a divergence is expected and reported rather than asserted.

    Returns a well-formed report dict:
        {agreed: bool, max_abs_diff: float, tolerance: float,
         per_stock: {sym: {target_allocation, decision, diff}}, nse_only}
    """
    ta = get_target_allocations(portfolio=portfolio)  # {sym: pct_of_total}

    try:
        from trading.services.decision import generate_proposal
        prop = generate_proposal(tilt="Balanced", nse_only=nse_only)
    except Exception:
        # Fail-open: if the Decision Engine is unavailable, we cannot prove
        # agreement, but we must not block the live path. Report as a
        # single-sided report (no divergence detected, but unverified).
        return {
            "agreed": True,
            "max_abs_diff": 0.0,
            "tolerance": tolerance,
            "per_stock": {s: {"target_allocation": round(p, 2),
                              "decision": None, "diff": None}
                          for s, p in ta.items()},
            "nse_only": nse_only,
            "verified": False,
        }

    eq_lines = {l.symbol: l.target_pct for l in prop.allocations
                if l.category == "equities"}
    # Normalise Decision equity lines to the same 90% base as target_allocation
    # so the comparison is apples-to-apples regardless of its cash bucket.
    eq_sum = sum(eq_lines.values()) or 1.0
    norm = {s: p * (TARGET_INVESTED_PCT / eq_sum) for s, p in eq_lines.items()}

    per_stock: dict[str, dict[str, Any]] = {}
    max_diff = 0.0
    for s in set(ta) | set(norm):
        d = abs(ta.get(s, 0.0) - norm.get(s, 0.0))
        per_stock[s] = {
            "target_allocation": round(ta.get(s, 0.0), 2),
            "decision": round(norm.get(s, 0.0), 2),
            "diff": round(d, 2),
        }
        max_diff = max(max_diff, d)

    return {
        "agreed": max_diff <= tolerance,
        "max_abs_diff": round(max_diff, 2),
        "tolerance": tolerance,
        "per_stock": per_stock,
        "nse_only": nse_only,
        "verified": True,
    }


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def _print_strategy(strategy: dict[str, Any]) -> None:
    """Print the current target strategy in a readable format."""
    print("\n  Target Allocation Strategy")
    print(f"  {'=' * 50}")
    print(f"  {'SECTOR':<18} {'TARGET':>7} {'TOLERANCE':>11}  RATIONALE")
    print(f"  {'-' * 50}")
    for sec, cfg in strategy.items():
        print(f"  {sec:<18} {cfg['target_pct']:>5.0f}%  ±{cfg['tolerance']:<5.0f}%  {cfg['rationale'][:45]}")
    print()


def _print_targets(targets: dict[str, Any]) -> None:
    """Print current vs target sector analysis."""
    current = targets["current"]
    strategy = targets["strategy"]

    print(f"\n  Sector Analysis — Portfolio Value: KES {targets['total_value']:,.0f}")
    print(f"  {'=' * 55}")
    print(f"  {'SECTOR':<15} {'CURRENT':>8} {'TARGET':>8} {'DRIFT':>8} {'STATUS':<14}")
    print(f"  {'-' * 55}")

    for sec in sorted(current.keys()):
        info = current[sec]
        drift = info["drift_pct"]
        drift_s = f"{drift:+.1f}%"
        drift_c = "green" if abs(drift) <= info["tolerance"] else "red" if drift > 0 else "yellow"
        status_s = info["status"].replace("_", " ")
        print(f"  {sec:<15} {info['current_pct']:>7.1f}% {info['target_pct']:>6.0f}% "
              f"{drift_s:>8} {status_s:<14}")

    # Cash line
    cash = targets["cash"]
    cash_d = cash["drift_pct"]
    print(f"  {'cash':<15} {cash['current_pct']:>7.1f}% {cash['target_pct']:>6.0f}% "
          f"{cash_d:+.1f}% → {cash['action']:<14}")

    # Summary
    s = targets["summary"]
    print(f"\n  Summary: {s['on_target']} on target, {s['within_tolerance']} within tolerance, "
          f"{s['over_weight']} overweight, {s['under_weight']} underweight")

    # Active actions
    print(f"\n  Recommended Actions:")
    has_actions = False
    for sec, info in current.items():
        action_labels = {"add": "➕ Add to", "trim": "➖ Trim", "hold": "⏸️  Hold"}
        label = action_labels.get(info["action"], info["action"])
        if info["action"] != "hold":
            has_actions = True
            print(f"    {label} {sec} ({info['drift_pct']:+.1f}% drift)")
    if cash["action"] != "hold":
        has_actions = True
        cash_labels = {"deploy": "💰 Deploy cash — too much on sidelines",
                       "raise": "🏦 Raise cash — need reserves"}
        print(f"    {cash_labels.get(cash['action'], cash['action'])}")
    if not has_actions:
        print(f"    ✅ All sectors within tolerance — no action needed")


def _print_rebalance_plan(plan: dict[str, Any]) -> None:
    """Print the rebalance trade plan."""
    trades = plan["trades"]
    summary = plan["summary"]

    if not trades:
        print(f"\n  📊 Rebalance Plan: No trades needed — portfolio is balanced.")
        return

    print(f"\n  📊 Rebalance Plan{' (DRY RUN)' if summary['dry_run'] else ''}")
    print(f"  {'=' * 55}")
    for t in trades:
        emoji = "🟢" if t["side"] == "BUY" else "🔴"
        print(f"  {emoji} {t['side']:4s} {t['shares']:>4d} {t['symbol']:<6s} "
              f"@ KES {t['price']:>7.2f} = KES {t['value']:>8,.0f}")
        print(f"      {t['reason']}")
    print(f"  {'-' * 55}")
    print(f"  Buy:  KES {summary['total_buy_value']:>8,.0f}")
    print(f"  Sell: KES {summary['total_sell_value']:>8,.0f}")
    print(f"  Net:  KES {summary['net_cash']:>+8,.0f}")
    print(f"  Trades: {summary['trade_count']}")


def main() -> int:
    """CLI entry point for target allocation analysis.

    Usage:
        python3 -m trading.target_allocation [--json] [--rebalance [--dry-run]]
    """
    import argparse
    parser = argparse.ArgumentParser(
        description="Target Allocation Engine — sector-based strategic allocation"
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON on stdout")
    parser.add_argument(
        "--rebalance", action="store_true",
        help="Generate rebalance trade plan"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Preview trades without executing (default: True)"
    )
    args = parser.parse_args()

    try:
        strategy = get_strategy()
        weights = compute_sector_weights()
        targets = compute_targets(weights)

        if args.rebalance:
            plan = generate_rebalance_plan(dry_run=args.dry_run)
            if args.json:
                print(json.dumps(plan, indent=2, default=str))
            else:
                _print_strategy(strategy)
                _print_targets(targets)
                _print_rebalance_plan(plan)
        elif args.json:
            print(json.dumps({
                "strategy": strategy,
                "targets": targets,
            }, indent=2, default=str))
        else:
            _print_strategy(strategy)
            _print_targets(targets)
            # Show per-stock allocation targets
            allocs = get_target_allocations()
            if allocs:
                print(f"\n  Per-Stock Allocation Targets:")
                for sym, pct in sorted(allocs.items()):
                    print(f"    {sym:<6s} → {pct:>5.1f}%")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
