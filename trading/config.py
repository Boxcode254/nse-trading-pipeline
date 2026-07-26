"""Configuration for the trading signal engine.

Pairs, indicator parameters, and filesystem paths. Keep this file
side-effect free so it can be imported from anywhere.
"""
from __future__ import annotations

import os
from typing import Any

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
    # Macro / volatility circuit breaker (NSE index / breadth / vol regime).
    # Fail-open: a missing/errored macro feed never trips the breaker.
    "macro_fail_open": True,
    "macro": {
        "index_drop_pct": 3.0,       # single-session NSE index drop that halts
        "breadth_min_pct": 20.0,     # advancers% floor before broad-selloff halt
        "vol_spike_multiple": 3.0,   # annualised vol ceiling (x100%) that halts
        "cooldown_seconds": 86_400,  # 24h before an auto-reconsideration
    },
    # Sector exposure cap (percent of portfolio) — used by auto_trader to force sells
    "max_sector_exposure_pct": 25.0,
    # Cash reserve: fraction of portfolio to keep uninvested (vs opportunities)
    "cash_reserve_pct": 20.0,
    # Daily deployment cap: max percent of portfolio to deploy in a single day
    "daily_deployment_cap_pct": 50.0,
    # Minimum trade size in KES to avoid dust
    "min_trade_kes": 1000.0,
    # Fee headroom: multiplicative factor to estimate fees (1.001 = 0.1% fee)
    "fee_headroom": 1.001,
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