"""Configuration for the trading signal engine.

Pairs, indicator parameters, and filesystem paths. Keep this file
side-effect free so it can be imported from anywhere.
"""
from __future__ import annotations

import csv
import os
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

# Pairs to monitor. Two forex + twelve NSE-listed Kenyan equities.
# Format is "BASE/QUOTE" for forex (e.g. "EUR/USD") and a plain ticker
# symbol for stocks (e.g. "SCOM"). The fetcher routes by asset class
# via get_asset_class(pair) (anything containing "/" is forex; everything
# else is stocks for now).
PAIRS: list[str] = [
    "EUR/USD",
    "USD/KES",
    "SCOM",
    "KCB",
    "EQTY",
    "EABL",
    "ABSA",
    "SCBK",
    "COOP",
    "KPLC",
    "TOTL",
    "KNRE",
    "WTK",
    "BAMB",
]

# ── News/monitoring-only watchlist ─────────────────────────────────
# Symbols we want tracked in the daily news scan + earnings calendar but
# that are NOT part of the tradable universe (auto-trader, strategy
# engine, ranking, allocation). Adding a ticker here will NEVER generate
# a buy/sell signal. Use this for "watch interesting NSE names" like
# Centum (CTUM) without risking unintended trades.
WATCHLIST_EXTRA: list[str] = [
    "CTUM",  # Centum Investment Company Plc (NSE:CTUM) — investment holding co.
]

# Indicator parameters
SMA_FAST: int = 20
SMA_SLOW: int = 50
RSI_PERIOD: int = 14
RSI_OVERBOUGHT: float = 70.0
RSI_OVERSOLD: float = 30.0

# Lookback days for data fetch. Stocks trend slower than forex, so we
# pull more history to give SMA(50) enough warm-up bars.
LOOKBACK_DAYS: int = 200

# Filesystem layout -- all under ~/.trading/ so the package is fully self-contained
HOME: str = os.path.expanduser("~/.trading")
DATA_DIR: str = os.path.join(HOME, "data")
CACHE_DIR: str = os.path.join(HOME, "cache")
DECISION_CACHE_DIR: str = os.path.join(HOME, "cache")
SIGNALS_CSV: str = os.path.join(HOME, "signals.csv")
BACKTEST_DIR: str = os.path.join(HOME, "backtests")

# Synthetic-data seed so the offline fallback is reproducible
SYNTHETIC_SEED: int = 1337

# How aggressive the synthetic random walk is (per-day vol, as fraction of price)
SYNTHETIC_VOL: float = 0.004

# ── Signal Validator thresholds ──────────────────────────────────────

# Minimum distance from RSI=50 required for a BUY/SELL signal.
# Example: 15 means BUY needs RSI > 65, SELL needs RSI < 35.
CONFIDENCE_MIN_RSI_DELTA: float = 15.0

# Maximum allowed intraday spread as a fraction of close price.
# Example: 0.02 means spread can be at most 2% of the close price.
CONFIRM_MAX_SPREAD_FRAC: float = 0.02

# Per-asset-class filter overrides. The validator checks ``asset`` on the
# signal dict (set by ``get_asset_class(pair)``) and applies the matching
# thresholds. Keys not present fall through to the top-level defaults.
ASSET_FILTERS: dict[str, dict[str, float]] = {
    "forex": {
        "min_volume": 0.0,  # spot forex has no centralized volume
    },
    "stocks": {
        "min_volume": 1_000_000.0,
    },
    "crypto": {
        "min_volume": 1_000.0,
    },
}

# Cooldown: skip duplicate signals for the same pair + direction within
# this many hours.
DUPLICATE_COOLDOWN_HOURS: int = 24

# Run-log directory — each run writes a JSON file here
LOGS_DIR: str = os.path.join(HOME, "logs")

# Map our human pair names to yfinance tickers. Forex pairs use the
# "=X" suffix; NSE-listed equities use the "NSE:" prefix. Exotic
# tickers or weekend closes fall back to deterministic synthetic data
# so the engine is never blocked.
YFINANCE_TICKERS: dict[str, str] = {
    "EUR/USD": "EURUSD=X",
    "USD/KES": "USDKES=X",  # may not work on yfinance; falls back to synthetic
    "SCOM": "NSE:SCOM",  # Safaricom PLC
    "KCB": "NSE:KCB",  # KCB Group
    "EQTY": "NSE:EQTY",  # Equity Group Holdings
    "EABL": "NSE:EABL",  # East African Breweries Limited
    "ABSA": "NSE:ABSA",  # Absa Bank Kenya
    "SCBK": "NSE:SCBK",  # Standard Chartered Bank Kenya
    "COOP": "NSE:COOP",  # Co-operative Bank of Kenya
    "KPLC": "NSE:KPLC",  # Kenya Power & Lighting Company
    "TOTL": "NSE:TOTL",  # TotalEnergies Kenya
    "KNRE": "NSE:KNRE",  # Kenya Reinsurance Corporation
    "WTK": "NSE:WTK",  # WPP ScanGroup
    "BAMB": "NSE:BAMB",  # Bamburi Cement
    "CTUM": "NSE:CTUM",  # Centum Investment Company (watch-only, see WATCHLIST_EXTRA)
}

# ── NSE Data Sources ─────────────────────────────────────────────────
# Multiple data sources are wired. The active one is set below.
#
# Currently active: "tradingview" (default) — free, no API key needed
#   Uses tradingview_ta library. All NSE stocks work live.
#
# Sources built/worked on:
# 1. "mystocks" — public scraper built (scripts/mystocks-scraper.py)
#    Scrapes https://live.mystocks.co.ke/stock={sym}, no login required.
#    Runs as pre-step in the morning briefing. 15-min delayed data.
#
# 2. "mansa" — Mansa API configured below via MANSA_API_KEY
#    Free tier: 100 req/day, no credit card at https://mansaapi.com
#    Exchange code "KENYA" covers 50 NSE stocks.
#    Used in gap scanner and auto-trader code.
#
# ❌ RapidAPI — considered but never built. Stub exists in fetchers/nse.py.
NSE_DATA_SOURCE: str = "tradingview"  # tradingview | mystocks | mansa

# Mansa API configuration for live NSE prices (primary source)
# Free tier: 100 req/day, no credit card needed at https://mansaapi.com
# Exchange code "KENYA" covers 50 NSE stocks
MANSA_API_KEY: str = os.environ.get("MANSA_API_KEY", "")
MANSA_BASE_URL: str = "https://mansaapi.com/api/v1"

# ── Gap Filter ─────────────────────────────────────────────────────────────
# Auto-trader skips trades when a stock gaps beyond this % (signal stale).
# Per-stock overrides in GAP_THRESHOLDS take precedence over the default.
# mystocks.co.ke is used as a cross-check source alongside Mansa API.
GAP_THRESHOLD_PCT: float = 3.0
GAP_THRESHOLDS: dict[str, float] = {
    "WTK": 5.0,    # volatile tea stock — wider gap needed to flag
    "KCB": 2.0,    # stable bank — smaller gap matters
    "EQTY": 2.0,
}

# ── Canonical sector map (single source of truth) ───────────────────
# Used by target_allocation, auto_trader sector caps, and reporting.
# Aligns with the target-allocation STRATEGY buckets:
#   banking / telecom / manufacturing / energy / insurance
# Symbols not listed in any STRATEGY.stocks set are "orphans" and may
# be force-exited by generate_rebalance_plan (see WTK).
SECTOR_MAP: dict[str, str] = {
    "SCOM": "telecom",
    "KCB": "banking",
    "EQTY": "banking",
    "ABSA": "banking",
    "SCBK": "banking",
    "COOP": "banking",
    # EABL kept manufacturing's remaining share as its own "consumer"
    # sector after BAMB excision (2026-07-20). Must match STRATEGY sector
    # vocabulary in target_allocation.py or compute_sector_weights buckets
    # it into a phantom sector and the floor guard misfires.
    "EABL": "consumer",
    "BAMB": "consumer",  # suspended NSE 28-Feb-2025; classified for reporting only
    "KPLC": "energy",
    "TOTL": "energy",
    "KNRE": "insurance",
    "BRIT": "insurance",
    "WTK": "services",  # held orphan — not in STRATEGY targets
}

# Suspended/halted symbols (e.g., BAMB). The auto-trader skips these.
SUSPENDED_SYMBOLS: list[str] = ["BAMB"]

def get_sector(symbol: str) -> str:
    """Return canonical sector for a symbol (default ``other``)."""
    return SECTOR_MAP.get(symbol, "other")

# Mystocks configuration (uncomment when ready)
# MYSTOCKS_API_KEY: str = os.environ.get("MYSTOCKS_API_KEY", "")
# MYSTOCKS_BASE_URL: str = "https://api.mystocks.co.ke/v1"

# ❌ RapidAPI — considered but never built. Removed.

# ── Market Ranking Engine ────────────────────────────────────────────
# Weights for the 8 scoring factors used by ranking/scorer.py. The
# sum should be 1.0; the values here are the "house view" on which
# factors matter most for an accumulator (long-biased) perspective.
SCORING_WEIGHTS: dict[str, float] = {
    "trend": 0.25,               # ★ Primary — backtest shows trend is king on NSE
    "momentum": 0.25,            # ★ Primary — 73% BH capture from momentum trend
    "volatility": 0.10,          # Low vol = stability
    "liquidity": 0.08,           # Volume confirms conviction
    "relative_strength": 0.12,   # Compare vs sector peers
    "risk": 0.08,                # Drawdown penalty
    "regime": 0.07,              # Market context
    "alignment": 0.05,           # Indicator agreement
}

# Recommendation tiers — score thresholds that map to a label.
# Mirrors the spec exactly: 90+/75+/50+/25+/0-.
TIER_STRONG_ACCUMULATE = "Strong Accumulate"
TIER_ACCUMULATE = "Accumulate"
TIER_HOLD = "Hold"
TIER_REDUCE = "Reduce"
TIER_AVOID = "Avoid"

RECOMMENDATION_THRESHOLDS: list[tuple[float, str]] = [
    (90.0, TIER_STRONG_ACCUMULATE),
    (75.0, TIER_ACCUMULATE),
    (50.0, TIER_HOLD),
    (25.0, TIER_REDUCE),
    (0.0, TIER_AVOID),
]

# Expected holding period (months) by tier.
HOLDING_PERIODS: dict[str, str] = {
    TIER_STRONG_ACCUMULATE: "6 months",
    TIER_ACCUMULATE: "12 months",
    TIER_HOLD: "18 months",
    TIER_REDUCE: "24 months",
    TIER_AVOID: "24 months",
}

# ── Execution Engine ─────────────────────────────────────────────────
EXECUTION_CONFIG: dict[str, Any] = {
    "max_trade_size_kes": 500_000.0,
    "max_daily_loss_kes": 100_000.0,
    "max_daily_loss_pct": 5.0,
    "max_single_exposure_pct": 25.0,
    "max_position_count": 20,
    "enabled": True,
    "default_broker": "paper",
    "state_dir": os.path.join(HOME, "execution"),
    # ── Phase 1 risk-gate thresholds (2026-07-25) ──
    # Block all new trades when the live MTM equity-curve drawdown hits this %.
    "max_drawdown_halt_pct": 15.0,
    # Stop-loss: a held position past this % loss (vs avg cost) blocks further
    # BUYs (no averaging down); SELLs stay allowed and are flagged.
    "stop_loss_pct": 8.0,
    # Take-profit: a held position up this % (vs avg cost) is fully exited
    # (sell the winner and leave). Full exit, not a partial trim. 0/None disables.
    "take_profit_pct": 20.0,
    # Macro / volatility circuit breaker (NSE index / breadth / vol regime).
    # Fail-open: a missing/errored macro feed never trips the breaker.
    "macro_fail_open": True,
    "macro": {
        "index_drop_pct": 3.0,       # single-session NSE index drop that halts
        "breadth_min_pct": 20.0,     # advancers% floor before broad-selloff halt
        "vol_spike_multiple": 3.0,   # annualised vol ceiling (x100%) that halts
        "cooldown_seconds": 86_400,  # 24h before an auto-reconsideration
    },
    # Sector exposure cap (percent of portfolio) — used by auto_trader to force sells.
    # REPLACED by per-sector SECTOR_CAPS (below) on 2026-08-04: a single flat 25%
    # force-sold banking even though the strategy targets ~49% banking, causing
    # constant trim pressure + stuck UNKNOWN sells. Retained as the DEFAULT_CAP
    # fallback for sectors not listed in SECTOR_CAPS.
    "max_sector_exposure_pct": 25.0,
    # Tiered per-sector concentration caps. Each sector gets a WARN (review flag)
    # and HARD (forced trim) ceiling. Sized to CORRELATED-DOWNSIDE tolerance, NOT
    # to "is the sector profitable" — winners are allowed to run up to HARD.
    # Banking is the book's core, most-researched edge (5 NSE names, correlated),
    # so it gets the highest ceiling; a correlated CBK/NPL shock still trims at 45%.
    "sector_caps": {
        "banking":   {"warn": 40.0, "hard": 45.0},
        "telecom":   {"warn": 25.0, "hard": 30.0},
        "energy":    {"warn": 25.0, "hard": 30.0},
        "insurance": {"warn": 20.0, "hard": 25.0},
        "consumer":  {"warn": 20.0, "hard": 25.0},
        "other":     {"warn": 15.0, "hard": 20.0},
    },
    # Momentum gate: when a sector is over its HARD cap BUT still trending up
    # (sector avg return over `momentum_lookback_days` >= `momentum_min_pct`),
    # do NOT force-trim — let the winner run (HARD effectively +10pts). Only trim
    # when momentum is fading. Reads PRICES only (never news). Keeps risk bounded
    # while not punishing uptrends. Set momentum_min_pct high (e.g. 99) to disable.
    "momentum_gate": {
        "enabled": True,
        "lookback_days": 20,
        "momentum_min_pct": 0.0,   # sector avg return >= this (over lookback) = still trending
        "hard_uplift_pct": 10.0,    # HARD cap raised by this when momentum is up
    },
    # Cash reserve: fraction of portfolio to keep uninvested (vs opportunities)
    "cash_reserve_pct": 10.0,
    # Daily deployment cap: max percent of portfolio to deploy in a single day
    "daily_deployment_cap_pct": 50.0,
    # Minimum trade size in KES to avoid dust
    "min_trade_kes": 1000.0,
    # ── Realistic NSE cost model (live-honest paper book) ──────────────────────
    # Per-side costs approximate the all-in NSE round-trip: brokerage (~1.0-1.5%,
    # discounted for value > KES 50k) + CDSC (0.012%) + statutory levy (0.0008%)
    # + stamp duty (0.0% on equities, but 0.05% on some) + VAT (16% on brokerage).
    # We model a SIMPLER, conservative proxy so the paper book doesn't overstate
    # edge: per-side % fee + a minimum commission floor (real brokers don't charge
    # fractional cents on small trades) + a slippage estimate (fills aren't at mid).
    "cost_model": {
        "per_side_pct": 1.5,        # % of trade value, per side (BUY and SELL each)
        "min_commission_kes": 60.0, # real brokers floor commission ~KES 50-100
        "slippage_pct": 0.15,       # est. fill slippage per side (spread/impact)
        "slippage_model": "pct_of_price",  # applied to execution price, not value
    },
    # Fee headroom: multiplicative factor to reserve fee+min-commission when sizing
    # buys. Must cover per_side_pct + min_commission headroom. 1.02 ≈ 2% buffer.
    "fee_headroom": 1.02,
}

# Backward-compatible constants (used by auto_trader.py)
MAX_SECTOR_EXPOSURE_PCT: float = EXECUTION_CONFIG["max_sector_exposure_pct"]
MAX_TRADE_SIZE_KES: float = EXECUTION_CONFIG["max_trade_size_kes"]
MAX_DAILY_LOSS_KES: float = EXECUTION_CONFIG["max_daily_loss_kes"]
MAX_DAILY_LOSS_PCT: float = EXECUTION_CONFIG["max_daily_loss_pct"]
MAX_SINGLE_EXPOSURE_PCT: float = EXECUTION_CONFIG["max_single_exposure_pct"]
MAX_POSITION_COUNT: int = EXECUTION_CONFIG["max_position_count"]
EXECUTION_ENABLED: bool = EXECUTION_CONFIG["enabled"]
DEFAULT_BROKER: str = EXECUTION_CONFIG["default_broker"]
STATE_DIR: str = EXECUTION_CONFIG["state_dir"]
MAX_DRAWDOWN_HALT_PCT: float = EXECUTION_CONFIG["max_drawdown_halt_pct"]
STOP_LOSS_PCT: float = EXECUTION_CONFIG["stop_loss_pct"]
TAKE_PROFIT_PCT: float = EXECUTION_CONFIG["take_profit_pct"]
MACRO_FAIL_OPEN: bool = EXECUTION_CONFIG["macro_fail_open"]
CASH_RESERVE_PCT: float = EXECUTION_CONFIG["cash_reserve_pct"]
DAILY_DEPLOYMENT_CAP_PCT: float = EXECUTION_CONFIG["daily_deployment_cap_pct"]
MIN_TRADE_KES: float = EXECUTION_CONFIG["min_trade_kes"]
FEE_HEADROOM: float = EXECUTION_CONFIG["fee_headroom"]

def get_asset_class(pair: str) -> str:
    """Derive the asset class from a pair name.

    Rule of thumb:
    - ``EUR/USD``, ``GBP/JPY`` etc (contains ``/``) → forex
    - Everything else → stocks (crypto pairs handled via explicit config later)
    """
    if "/" in pair:
        return "forex"
    return "stocks"

# ── Asset classification (Decision Engine) ─────────────────────────
# Each monitored pair is mapped to a category (equities / forex / cash /
# commodity) and a sub-sector. The decision engine uses these to build
# the holistic allocation proposal.
#
# Sentinel symbols ``__cash__`` and ``__gold__`` represent the cash
# buffer and a strategic gold recommendation. They are never
# price-fetched; they only exist as allocation targets.
ASSET_CATEGORIES: dict[str, dict[str, str]] = {
    # Equities — NSE-listed Kenyan stocks
    "SCOM": {"category": "equities", "sector": "telecom",  "display": "Safaricom"},
    "KCB":  {"category": "equities", "sector": "banking",  "display": "KCB"},
    "EQTY": {"category": "equities", "sector": "banking",  "display": "Equity"},
    "EABL": {"category": "equities", "sector": "consumer", "display": "EABL"},
    "ABSA": {"category": "equities", "sector": "banking",  "display": "Absa"},
    "SCBK": {"category": "equities", "sector": "banking",  "display": "Stanchart"},
    "COOP": {"category": "equities", "sector": "banking",  "display": "Co-op Bank"},
    "KPLC": {"category": "equities", "sector": "energy", "display": "KPLC"},
    "TOTL": {"category": "equities", "sector": "energy",   "display": "TotalEnergies"},
    "KNRE": {"category": "equities", "sector": "insurance","display": "Kenya Re"},
    "WTK":  {"category": "equities", "sector": "services", "display": "WPP ScanGroup"},
    "BAMB": {"category": "equities", "sector": "consumer", "display": "Bamburi"},
    # Forex — major + KES crosses
    "EUR/USD": {"category": "forex", "sector": "major",     "display": "EUR/USD"},
    "USD/KES": {"category": "forex", "sector": "em-fx",     "display": "USD/KES"},
    # Strategic categories (no price feed)
    "__cash__": {"category": "cash",      "sector": "buffer",   "display": "Cash (KES)"},
    "__gold__": {"category": "commodity", "sector": "precious", "display": "Gold"},
    "__tbills__": {"category": "fixed_income", "sector": "government", "display": "T-Bills"},
}

# Order in which lines are rendered in the decision CLI (cash + gold
# always last as strategic rails).
ASSET_DISPLAY_ORDER: list[str] = [
    "SCOM", "KCB", "EQTY", "EABL", "ABSA", "SCBK",
    "COOP", "KPLC", "TOTL", "KNRE", "WTK", "BAMB",
    "EUR/USD", "USD/KES",
    "__gold__", "__tbills__", "__cash__",
]

def get_asset_category(symbol: str) -> dict[str, str]:
    """Return the category/sector/display for a symbol.

    Falls back to a generic ``"other"`` record for unknown symbols so
    the decision engine never crashes on a new ticker. Forex pairs
    (anything containing "/") are auto-categorised when not present
    in the explicit table.
    """
    if symbol in ASSET_CATEGORIES:
        return ASSET_CATEGORIES[symbol]
    if "/" in symbol:
        return {"category": "forex", "sector": "other", "display": symbol}
    return {"category": "equities", "sector": "other", "display": symbol}

def get_equity_symbols() -> list[str]:
    """Return the configured equity symbols (NSE tickers) only."""
    return [s for s, meta in ASSET_CATEGORIES.items()
            if meta["category"] == "equities" and not s.startswith("__")]

def get_forex_symbols() -> list[str]:
    """Return the configured forex pairs only."""
    return [s for s, meta in ASSET_CATEGORIES.items()
            if meta["category"] == "forex" and not s.startswith("__")]

def ensure_dirs() -> None:
    """Create the on-disk layout the package expects. Idempotent."""
    for path in (HOME, DATA_DIR, BACKTEST_DIR, LOGS_DIR):
        os.makedirs(path, exist_ok=True)


# ── Tiered sector-cap resolver ──────────────────────────────────────────────
# Centralises the per-sector WARN/HARD caps (+ momentum uplift) so auto_trader
# and target_allocation enforce the SAME numbers. Reads PRICES only (never news)
# for the momentum gate.
#
# TP-002 (2026-09-15) — the momentum uplift was silently disabled. Two defects:
#   1. ``DATA_DIR`` is a ``str``, so ``DATA_DIR / f"nse_{sym}.csv"`` raised
#      TypeError on the first member of every sector and the broad
#      ``except Exception: pass`` hid it (no uplift was ever applied).
#   2. There was no freshness contract: a months-old CSV could have granted a
#      risk-ceiling uplift.
# Both are repaired below. No cap, sector target, threshold, strategy weight or
# evaluation gate was changed.

# A CSV whose last session is older than this many calendar days cannot grant a
# hard-cap uplift. The uplift RELAXES a risk ceiling, so it may only be granted
# on fresh evidence. This is a safety bound, not a tuning knob; override per call
# or via momentum_gate.max_staleness_days.
MOMENTUM_MAX_STALENESS_DAYS: int = 7

_CSV_DATE_KEYS = ("date", "Date", "DATE", "timestamp", "time", "as_of")

# Diagnostics that must reach a human/operator instead of being swallowed.
_MOMENTUM_DATA_PROBLEM_STATES = ("missing", "unreadable", "malformed", "stale")


def _as_date(value) -> date:
    """Normalise ``None``/``date``/``datetime`` to a ``date``.

    Raises TypeError for anything else: a bad ``now=`` is a programmer error and
    must not be silently replaced by "today" (that would make a freshness check
    unverifiable).
    """
    if value is None:
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(
        f"sector momentum gate: now= must be a date/datetime, got {type(value).__name__} ({value!r})"
    )


def _row_close(row: dict) -> Optional[float]:
    """Return the close-like value of a CSV row, or None when unreadable."""
    value = _num(row)
    if not value or value <= 0:
        return None
    return float(value)


def _row_date(row: dict) -> tuple[Optional[date], str]:
    """Return ``(session_date, state)`` for a CSV row.

    state is ``"ok"`` (parsed from a date column), ``"no_date_column"`` (the
    caller may fall back to the cache file mtime) or ``"malformed"`` (a date
    column exists but the value cannot be read — never treated as fresh).
    """
    raw = None
    for key in _CSV_DATE_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            raw = value
            break
    if raw is None:
        return None, "no_date_column"
    text = str(raw).strip()
    for candidate in (text, text[:10]):
        try:
            return datetime.fromisoformat(candidate).date(), "ok"
        except ValueError:
            continue
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date(), "ok"
        except ValueError:
            continue
    return None, "malformed"


def sector_momentum_diagnostics(
    sector: str,
    *,
    data_dir=None,
    now=None,
    max_staleness_days: Optional[int] = None,
) -> dict:
    """Explain WHY the sector momentum uplift did or did not apply. Read-only.

    Returns a dict carrying ``applied`` (bool), a machine-readable ``reason``,
    ``blocking`` (the data problems found, if any) and the per-member price
    evidence behind the decision, so missing / unreadable / malformed / stale
    input is inspectable rather than silently disabling the gate.

    Conservative by construction: any member that is missing, unreadable,
    malformed, future-dated or stale blocks the uplift for the whole sector. A
    path-type error in ``data_dir`` raises TypeError instead of being swallowed.
    """
    resolved_upto = _as_date(now)

    def _blank(reason: str, detail: str) -> dict:
        return {
            "sector": sector,
            "applied": False,
            "reason": reason,
            "detail": detail,
            "blocking": [],
            "uplift_pct": None,
            "effective_hard_uplift": 0.0,
            "lookback_days": None,
            "momentum_min_pct": None,
            "max_staleness_days": None,
            "avg_return_pct": None,
            "members": [],
            "evaluated_at": resolved_upto.isoformat(),
        }

    gate = EXECUTION_CONFIG.get("momentum_gate", {}) or {}
    if not gate.get("enabled", False):
        return _blank("gate_disabled", "momentum_gate.enabled is false")

    try:
        lookback = int(gate.get("lookback_days", 20))
        min_pct = float(gate.get("momentum_min_pct", 0.0))
        uplift = float(gate.get("hard_uplift_pct", 10.0))
        max_age = MOMENTUM_MAX_STALENESS_DAYS if max_staleness_days is None else int(max_staleness_days)
    except (TypeError, ValueError) as exc:
        # A malformed gate config is a config/programming error, not market data.
        # Fail closed (no uplift) but never silently.
        diag = _blank(
            "invalid_gate_config",
            f"momentum_gate config is not numeric: {exc!r}",
        )
        diag["blocking"] = ["invalid_gate_config"]
        warnings.warn(
            f"sector momentum uplift disabled for {sector!r}: {diag['detail']}",
            RuntimeWarning,
            stacklevel=2,
        )
        return diag

    raw_dir = DATA_DIR if data_dir is None else data_dir
    try:
        root = Path(raw_dir)
    except TypeError as exc:
        # Path-type error: raise loudly, do NOT degrade to "no uplift, no message".
        raise TypeError(
            f"sector momentum gate: data_dir must be path-like, got "
            f"{type(raw_dir).__name__} ({raw_dir!r})"
        ) from exc

    members = [s for s, sec in SECTOR_MAP.items() if sec == sector]
    per_member: list[dict] = []
    returns: list[float] = []
    blocking: list[str] = []

    for sym in members:
        path = root / f"nse_{sym}.csv"
        entry = {
            "symbol": sym,
            "path": str(path),
            "state": "ok",
            "rows": 0,
            "window_rows": 0,
            "date_source": None,
            "last_date": None,
            "staleness_days": None,
            "return_pct": None,
            "detail": "",
        }
        if not path.exists():
            entry.update(state="missing", detail="no cached CSV for this symbol")
            blocking.append("missing")
            per_member.append(entry)
            continue
        try:
            with path.open("r", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            entry.update(state="unreadable", detail=f"{type(exc).__name__}: {exc}")
            blocking.append("unreadable")
            per_member.append(entry)
            continue

        entry["rows"] = len(rows)
        if len(rows) < 2:
            entry.update(state="malformed", detail=f"needs >= 2 rows, found {len(rows)}")
            blocking.append("malformed")
            per_member.append(entry)
            continue

        window = min(lookback + 1, len(rows))
        entry["window_rows"] = window
        last_close = _row_close(rows[-1])
        prev_close = _row_close(rows[-window])
        if last_close is None or prev_close is None:
            entry.update(state="malformed", detail="close column missing or unparseable")
            blocking.append("malformed")
            per_member.append(entry)
            continue

        session_date, date_state = _row_date(rows[-1])
        if date_state == "malformed":
            entry.update(state="malformed", detail="date column present but unparseable")
            blocking.append("malformed")
            per_member.append(entry)
            continue
        if date_state == "no_date_column":
            try:
                session_date = datetime.fromtimestamp(path.stat().st_mtime).date()
            except OSError as exc:
                entry.update(state="unreadable", detail=f"stat failed: {exc}")
                blocking.append("unreadable")
                per_member.append(entry)
                continue
            entry["date_source"] = "mtime"
        else:
            entry["date_source"] = "csv"

        entry["last_date"] = session_date.isoformat()
        staleness = (resolved_upto - session_date).days
        entry["staleness_days"] = staleness
        if staleness < 0:
            entry.update(state="malformed", detail=f"last session {session_date} is dated in the future")
            blocking.append("malformed")
            per_member.append(entry)
            continue
        if staleness > max_age:
            entry.update(
                state="stale",
                detail=f"last session {session_date} is {staleness}d old (max {max_age}d)",
            )
            blocking.append("stale")
            per_member.append(entry)
            continue

        ret_pct = (last_close - prev_close) / prev_close * 100.0
        entry["return_pct"] = round(ret_pct, 4)
        per_member.append(entry)
        returns.append(ret_pct)

    diag = {
        "sector": sector,
        "applied": False,
        "reason": None,
        "detail": "",
        "blocking": sorted(set(blocking)),
        "uplift_pct": uplift,
        "effective_hard_uplift": 0.0,
        "lookback_days": lookback,
        "momentum_min_pct": min_pct,
        "max_staleness_days": max_age,
        "avg_return_pct": None,
        "members": per_member,
        "evaluated_at": resolved_upto.isoformat(),
        "data_dir": str(root),
    }

    if not members:
        diag.update(reason="no_members", detail=f"no sector members configured for {sector!r}")
    elif blocking:
        diag.update(
            reason="data_unusable",
            detail="no uplift — unusable price evidence: "
            + ", ".join(f"{e['symbol']}={e['state']}" for e in per_member if e["state"] != "ok"),
        )
    else:
        avg = sum(returns) / len(returns)
        diag["avg_return_pct"] = round(avg, 4)
        if avg >= min_pct:
            diag.update(
                applied=True,
                reason="uplift_applied",
                effective_hard_uplift=uplift,
                detail=(
                    f"{sector} avg {lookback}-session return {avg:.2f}% >= "
                    f"{min_pct:.2f}% — HARD cap raised by {uplift:.1f}pt"
                ),
            )
        else:
            diag.update(
                reason="momentum_below_threshold",
                detail=(
                    f"{sector} avg {lookback}-session return {avg:.2f}% < "
                    f"{min_pct:.2f}% — no uplift"
                ),
            )

    if diag["reason"] == "data_unusable":
        warnings.warn(
            f"sector momentum uplift disabled for {sector!r}: {diag['detail']}",
            RuntimeWarning,
            stacklevel=2,
        )
    return diag


def sector_cap(sector: str, *, data_dir=None, now=None, max_staleness_days: Optional[int] = None) -> dict:
    """Return {warn, hard} for a sector, applying the momentum uplift if trending.

    Falls back to DEFAULT_CAP (max_sector_exposure_pct) for unknown sectors.
    The momentum gate raises HARD by ``hard_uplift_pct`` (exactly once) when the
    sector's average return over ``lookback_days`` is >= ``momentum_min_pct``,
    so winning sectors are NOT force-trimmed at HARD. Reads price history from
    ``<DATA_DIR>/nse_<SYM>.csv``; every member must exist, be readable, be
    well-formed and be fresh, otherwise no uplift (conservative).

    ``data_dir`` / ``now`` / ``max_staleness_days`` exist for deterministic
    testing and inspection; production callers use the defaults. Call
    :func:`sector_momentum_diagnostics` with the same arguments to see why the
    uplift applied or was withheld.
    """
    caps = EXECUTION_CONFIG.get("sector_caps", {})
    default = float(EXECUTION_CONFIG.get("max_sector_exposure_pct", 25.0))
    base = caps.get(sector, {"warn": default, "hard": default})
    warn = float(base.get("warn", default))
    hard = float(base.get("hard", default))

    diag = sector_momentum_diagnostics(
        sector,
        data_dir=data_dir,
        now=now,
        max_staleness_days=max_staleness_days,
    )
    if diag["applied"]:
        hard += float(diag["effective_hard_uplift"])
    return {"warn": warn, "hard": hard}


def _num(row: dict) -> float:
    """Extract a close-like numeric from a CSV row (handles column variants)."""
    for key in ("close", "Close", "price", "Price"):
        v = row.get(key)
        if v in (None, ""):
            continue
        try:
            return float(str(v).replace(",", ""))
        except (ValueError, AttributeError):
            continue
    vals = list(row.values())
    if len(vals) > 4:
        try:
            return float(str(vals[4]).replace(",", ""))
        except (ValueError, AttributeError):
            return 0.0
    return 0.0


# ── Realistic trade-cost model (live-honest paper book) ────────────────────────
# Single source of truth for BUY/SELL costs so the engine and auto-trader report
# the SAME realistic drag. Returns (fee, slippage, total_cost) for a trade of
# `value` KES at `price`. Per-side % + minimum commission floor + slippage.
def trade_cost(value: float, price: float = 0.0) -> dict:
    """Estimate realistic NSE trade cost for a KES `value` trade.

    Components (per side):
      - per_side_pct of trade value (brokerage+CDSC+levy+VAT proxy)
      - min_commission_kes floor (real brokers don't charge fractional cents)
      - slippage: slippage_pct of price, applied to the fill (not value)
    Returns {fee, slippage, total} where total = fee + slippage*value proxy.
    On any error, falls back to a conservative 1.5% flat fee (no floor).
    """
    cm = EXECUTION_CONFIG.get("cost_model", {}) or {}
    try:
        pct = float(cm.get("per_side_pct", 1.5)) / 100.0
        floor = float(cm.get("min_commission_kes", 60.0))
        slip_pct = float(cm.get("slippage_pct", 0.15)) / 100.0
        fee = max(value * pct, floor)
        # slippage expressed as KES: slip_pct of price * implied shares (value/price)
        slip = 0.0
        if price and price > 0:
            slip = (value / price) * price * slip_pct  # = value * slip_pct
        return {
            "fee": round(fee, 2),
            "slippage": round(slip, 2),
            "total": round(fee + slip, 2),
        }
    except Exception:
        return {"fee": round(value * 0.015, 2), "slippage": 0.0, "total": round(value * 0.015, 2)}