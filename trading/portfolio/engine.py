"""Paper Portfolio Manager — engine module.

The portfolio is a flat-file ledger stored under ``~/.trading/portfolio/``:

* ``state.json``     — current cash + open positions
* ``transactions.json`` — append-only BUY/SELL ledger
* ``snapshots.json`` — mark-to-market history
* ``benchmark.json`` — buy-and-hold basket, recomputed each snapshot

This module owns all business logic: state I/O, position math, drawdown
calculation, benchmark recomputation, and CSV export. The CLI layer is
a thin wrapper over these functions.

Design rules (from the spec):
- No short selling — sell only what you hold.
- 0.1% transaction fee on each trade (rounded to 2 dp).
- Integer share quantities only.
- Decisions log is append-only (no editing history).
- ``--force`` on init wipes and starts fresh.
- All commands support ``--json`` for machine output.
"""
from __future__ import annotations

import json
import os
import csv
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# Defaults — overridable via init_portfolio(capital=...)
def _default_portfolio_dir() -> str:
    """Resolve the default portfolio dir at call time so HOME overrides work."""
    return os.path.expanduser("~/.trading/portfolio")

# Backwards-compatible constant — used by external callers that imported
# the name before refactor. Resolved once at import to the *initial* HOME.
DEFAULT_PORTFOLIO_DIR: str = _default_portfolio_dir()

DEFAULT_CAPITAL = 100_000.0
# Realistic trade cost is now sourced from config.cost_model via config.trade_cost()
# (per-side % + minimum commission floor + slippage). The old flat
# TRANSACTION_FEE_PCT/FEE_MIN constants are removed. See trading/config.py.


# ── Errors ─────────────────────────────────────────────────────────────────
class PortfolioError(Exception):
    """Base error for all portfolio operations."""


class PortfolioExistsError(PortfolioError):
    """Raised when init is called on an existing portfolio without --force."""


class InsufficientCashError(PortfolioError):
    """Raised on a BUY that would overdraw the cash balance."""


class InsufficientSharesError(PortfolioError):
    """Raised on a SELL of more shares than held."""


class UnknownPositionError(PortfolioError):
    """Raised on a SELL of a symbol not held."""


# ── Data classes ───────────────────────────────────────────────────────────
@dataclass
class Position:
    symbol: str
    shares: int
    avg_cost: float  # KES per share, weighted average
    total_cost: float  # shares * avg_cost (cached for fast total)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Persist a non-zero current_value so it's always reported even for
        # suspended / no-price names. Fall back to cost basis (total_cost)
        # when no live price is available — the caller (MTM layer) enriches
        # separately in mtm_state.json with live prices and P&L.
        d["current_value"] = round(d["total_cost"], 2)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        return cls(
            symbol=str(d["symbol"]),
            shares=int(d["shares"]),
            avg_cost=float(d["avg_cost"]),
            total_cost=float(d.get("total_cost", d["shares"] * d["avg_cost"])),
        )


@dataclass
class Transaction:
    timestamp: str
    symbol: str
    action: str  # BUY or SELL
    shares: int
    price: float
    total: float  # gross trade value (shares * price)
    fee: float
    net_cash_delta: float  # cash impact: BUY negative (total+fee), SELL positive (total-fee)
    reason: str = ""
    signal_ref: dict[str, Any] = field(default_factory=dict)
    realised_pnl: Optional[float] = None  # populated on SELL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transaction":
        return cls(
            timestamp=str(d["timestamp"]),
            symbol=str(d["symbol"]),
            action=str(d["action"]),
            shares=int(d["shares"]),
            price=float(d["price"]),
            total=float(d["total"]),
            fee=float(d.get("fee", 0.0)),
            net_cash_delta=float(d.get("net_cash_delta", 0.0)),
            reason=str(d.get("reason", "")),
            signal_ref=dict(d.get("signal_ref", {})),
            realised_pnl=(
                float(d["realised_pnl"])
                if d.get("realised_pnl") is not None
                else None
            ),
        )


@dataclass
class PortfolioState:
    initial_capital: float
    cash: float
    positions: list[Position] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    max_drawdown_pct: float = 0.0  # cached from latest snapshot

    def position_for(self, symbol: str) -> Optional[Position]:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None

    def total_cost_basis(self) -> float:
        return sum(p.total_cost for p in self.positions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "positions": [p.to_dict() for p in self.positions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "max_drawdown_pct": self.max_drawdown_pct,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PortfolioState":
        return cls(
            initial_capital=float(d["initial_capital"]),
            cash=float(d["cash"]),
            positions=[Position.from_dict(p) for p in d.get("positions", [])],
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
            max_drawdown_pct=float(d.get("max_drawdown_pct", 0.0)),
        )


@dataclass
class Snapshot:
    timestamp: str
    cash: float
    holdings_value: float
    total_value: float
    daily_return_pct: float
    total_return_pct: float
    drawdown_pct: float
    benchmark_value: float
    prices: dict[str, float] = field(default_factory=dict)  # symbol -> last close

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Snapshot":
        return cls(
            timestamp=str(d["timestamp"]),
            cash=float(d["cash"]),
            holdings_value=float(d["holdings_value"]),
            total_value=float(d["total_value"]),
            daily_return_pct=float(d.get("daily_return_pct", 0.0)),
            total_return_pct=float(d.get("total_return_pct", 0.0)),
            drawdown_pct=float(d.get("drawdown_pct", 0.0)),
            benchmark_value=float(d.get("benchmark_value", 0.0)),
            prices=dict(d.get("prices", {})),
        )


# ── Filesystem helpers ────────────────────────────────────────────────────
def _portfolio_dir(dir_path: Optional[str] = None) -> Path:
    p = Path(dir_path) if dir_path else Path(_default_portfolio_dir())
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path(dir_path: Optional[str] = None) -> Path:
    return _portfolio_dir(dir_path) / "state.json"


def _txn_path(dir_path: Optional[str] = None) -> Path:
    return _portfolio_dir(dir_path) / "transactions.json"


def _snap_path(dir_path: Optional[str] = None) -> Path:
    return _portfolio_dir(dir_path) / "snapshots.json"


def _bench_path(dir_path: Optional[str] = None) -> Path:
    return _portfolio_dir(dir_path) / "benchmark.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
    os.replace(tmp, path)
    # Lock permissions: owner rw, group read-only so portfolio.engine
    # (running as trading user) can write and hermes user can read.
    # Group is inherited from the parent directory so both users can access.
    try:
        os.chmod(path, 0o640)
        parent_gid = path.parent.stat().st_gid
        if path.stat().st_gid != parent_gid:
            os.chown(path, -1, parent_gid)
    except OSError:
        pass  # best-effort; don't break writes if hardening fails


# ── Public API ────────────────────────────────────────────────────────────
def portfolio_exists(dir_path: Optional[str] = None) -> bool:
    """True if a state.json is present in the portfolio directory."""
    return _state_path(dir_path).exists()


def init_portfolio(
    capital: float = DEFAULT_CAPITAL,
    force: bool = False,
    dir_path: Optional[str] = None,
    benchmark_assets: Optional[list[str]] = None,
) -> PortfolioState:
    """Create a fresh paper portfolio.

    Errors if the portfolio already exists unless ``force=True``.
    Returns the new state.
    """
    if portfolio_exists(dir_path) and not force:
        raise PortfolioExistsError(
            "Portfolio already exists. Use --force to reset, or run 'trading portfolio show' to inspect."
        )

    if capital <= 0:
        raise PortfolioError(f"Initial capital must be > 0, got {capital}")

    now = _now_iso()
    state = PortfolioState(
        initial_capital=round(float(capital), 2),
        cash=round(float(capital), 2),
        positions=[],
        created_at=now,
        updated_at=now,
    )
    _write_json(_state_path(dir_path), state.to_dict())
    _write_json(_txn_path(dir_path), [])
    _write_json(_snap_path(dir_path), [])

    # Benchmark definition: equal basket of all monitored assets by default
    from .. import config as _config  # local import to avoid circular
    assets = benchmark_assets if benchmark_assets is not None else list(_config.PAIRS)
    benchmark = {
        "initial_capital": state.initial_capital,
        "assets": list(assets),
        "init_prices": {},  # populated on first snapshot
        "snapshots": [],
    }
    _write_json(_bench_path(dir_path), benchmark)

    # Take the initial snapshot so the series is non-empty and total_value == cash
    take_snapshot(prices={}, dir_path=dir_path)
    return state


def load_state(dir_path: Optional[str] = None) -> PortfolioState:
    """Load the current portfolio state. Raises PortfolioError if not initialised."""
    if not portfolio_exists(dir_path):
        raise PortfolioError(
            "No portfolio found. Run 'trading portfolio init --capital 100000' first."
        )
    raw = _read_json(_state_path(dir_path), default=None)
    if raw is None:
        raise PortfolioError("state.json is corrupt or unreadable")
    return PortfolioState.from_dict(raw)


def load_transactions(dir_path: Optional[str] = None) -> list[Transaction]:
    """Return the full append-only transaction log."""
    raw = _read_json(_txn_path(dir_path), default=[])
    if not isinstance(raw, list):
        return []
    return [Transaction.from_dict(t) for t in raw]


def load_snapshots(dir_path: Optional[str] = None) -> list[Snapshot]:
    """Return the mark-to-market snapshot series, oldest first."""
    raw = _read_json(_snap_path(dir_path), default=[])
    if not isinstance(raw, list):
        return []
    return [Snapshot.from_dict(s) for s in raw]


def load_benchmark(dir_path: Optional[str] = None) -> dict[str, Any]:
    """Return the benchmark record (initial_capital, assets, init_prices, snapshots)."""
    return _read_json(_bench_path(dir_path), default={
        "initial_capital": 0.0, "assets": [], "init_prices": {}, "snapshots": [],
    })


def _append_transaction(txn: Transaction, dir_path: Optional[str] = None) -> None:
    log = load_transactions(dir_path)
    log.append(txn)
    _write_json(_txn_path(dir_path), [t.to_dict() for t in log])


def _log_state_change(
    before: dict | None,
    after: dict,
    dir_path: Optional[str] = None,
) -> None:
    """Append a one-line diff to the state-change journal.

    Format (grepable, one entry per write):
        TS | cash: B→A | positions: N→N | delta: +SYM(k),-SYM(k) | caller: ?
    """
    now = _now_iso()
    cash_b = before["cash"] if before else 0
    cash_a = after["cash"]
    pos_b = {p["symbol"]: p["shares"] for p in (before.get("positions", []) if before else [])}
    pos_a = {p["symbol"]: p["shares"] for p in after.get("positions", [])}

    added = [f"+{s}({pos_a[s]})" for s in pos_a if s not in pos_b]
    removed = [f"-{s}" for s in pos_b if s not in pos_a]
    delta = " ".join(added + removed) if (added or removed) else "─"

    log_line = (
        f"{now} | cash: {cash_b}→{cash_a} | "
        f"positions: {len(pos_b)}→{len(pos_a)} | "
        f"delta: {delta}"
    )

    log_path = Path(dir_path or _default_portfolio_dir()) / "state_changes.log"
    try:
        with open(log_path, "a") as f:
            f.write(log_line + "\n")
    except OSError:
        pass  # best-effort; don't break writes if logging fails


def _save_state(state: PortfolioState, dir_path: Optional[str] = None) -> None:
    state.updated_at = _now_iso()
    state_path = _state_path(dir_path)

    # Load previous state for diff logging
    prev: dict | None = None
    if state_path.exists():
        try:
            with open(state_path) as f:
                prev = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

    _write_json(state_path, state.to_dict())

    # Log before/after diff to state_changes.log
    _log_state_change(prev, state.to_dict(), dir_path)


# ── Trading primitives ────────────────────────────────────────────────────
def buy(
    symbol: str,
    shares: int,
    price: float,
    reason: str = "",
    signal_ref: Optional[dict[str, Any]] = None,
    dir_path: Optional[str] = None,
) -> tuple[PortfolioState, Transaction]:
    """Record a paper BUY. Updates state + transaction log.

    Raises InsufficientCashError if total + fee exceeds available cash.
    """
    if shares <= 0:
        raise PortfolioError(f"Shares must be > 0, got {shares}")
    if price <= 0:
        raise PortfolioError(f"Price must be > 0, got {price}")

    state = load_state(dir_path)
    total = round(shares * price, 2)
    from .. import config as _cfg
    cost_info = _cfg.trade_cost(total, price)
    fee = cost_info["fee"]
    slip = cost_info["slippage"]
    # Slippage raises the effective buy price (you pay more than mid).
    effective_price = round(price * (1 + (slip / total if total else 0)), 4) if total else price
    cost = round(total + fee + slip, 2)

    if cost > state.cash + 0.0001:
        raise InsufficientCashError(
            f"Insufficient cash: need KES {cost:,.2f} (incl. fee), have KES {state.cash:,.2f}"
        )

    new_cash = round(state.cash - cost, 2)
    existing = state.position_for(symbol)
    effective_total = round(total + slip, 2)  # mid value + slippage paid
    if existing is None:
        state.positions.append(Position(
            symbol=symbol, shares=shares, avg_cost=effective_price, total_cost=effective_total,
        ))
    else:
        # Weighted-average cost basis (use effective cost incl. slippage)
        new_total_shares = existing.shares + shares
        new_total_cost = round(existing.total_cost + effective_total, 2)
        new_avg = round(new_total_cost / new_total_shares, 4)
        existing.shares = new_total_shares
        existing.avg_cost = new_avg
        existing.total_cost = new_total_cost

    state.cash = new_cash
    _save_state(state, dir_path)

    txn = Transaction(
        timestamp=_now_iso(),
        symbol=symbol,
        action="BUY",
        shares=shares,
        price=effective_price,
        total=total,
        fee=fee,
        net_cash_delta=-cost,
        reason=reason,
        signal_ref=dict(signal_ref or {}),
        realised_pnl=None,
    )
    _append_transaction(txn, dir_path)
    return state, txn


def sell(
    symbol: str,
    shares: Optional[int],
    price: float,
    reason: str = "",
    signal_ref: Optional[dict[str, Any]] = None,
    dir_path: Optional[str] = None,
) -> tuple[PortfolioState, Transaction]:
    """Record a paper SELL. If shares is None, sells the entire position.

    Raises UnknownPositionError if symbol is not held, or
    InsufficientSharesError if requested shares exceed holding.
    """
    if price <= 0:
        raise PortfolioError(f"Price must be > 0, got {price}")

    state = load_state(dir_path)
    existing = state.position_for(symbol)
    if existing is None:
        raise UnknownPositionError(f"No position in {symbol} to sell")

    if shares is None:
        shares_to_sell = existing.shares
    else:
        if shares <= 0:
            raise PortfolioError(f"Shares must be > 0, got {shares}")
        if shares > existing.shares:
            raise InsufficientSharesError(
                f"Cannot sell {shares} shs of {symbol}; only {existing.shares} held"
            )
        shares_to_sell = shares

    proceeds = round(shares_to_sell * price, 2)
    from .. import config as _cfg
    cost_info = _cfg.trade_cost(proceeds, price)
    fee = cost_info["fee"]
    slip = cost_info["slippage"]
    # Slippage lowers the effective sell price (you receive less than mid).
    effective_sell_price = round(price * (1 - (slip / proceeds if proceeds else 0)), 4) if proceeds else price
    net = round(proceeds - fee - slip, 2)
    realised = round((effective_sell_price - existing.avg_cost) * shares_to_sell, 2)

    new_cash = round(state.cash + net, 2)
    remaining_shares = existing.shares - shares_to_sell
    if remaining_shares == 0:
        state.positions = [p for p in state.positions if p.symbol != symbol]
    else:
        # Reduce cost basis proportionally
        ratio = remaining_shares / existing.shares
        existing.shares = remaining_shares
        existing.total_cost = round(existing.total_cost * ratio, 2)
        # avg_cost unchanged

    state.cash = new_cash
    _save_state(state, dir_path)

    txn = Transaction(
        timestamp=_now_iso(),
        symbol=symbol,
        action="SELL",
        shares=shares_to_sell,
        price=effective_sell_price,
        total=proceeds,
        fee=fee,
        net_cash_delta=net,
        reason=reason,
        signal_ref=dict(signal_ref or {}),
        realised_pnl=realised,
    )
    _append_transaction(txn, dir_path)
    return state, txn


# ── Snapshots + drawdown ──────────────────────────────────────────────────
def compute_drawdown(snapshots: list[Snapshot]) -> list[float]:
    """Return per-snapshot drawdown % from the running peak of total_value.

    Index alignment: ``snapshots[0]`` always has drawdown 0 (it's the
    first peak). Each subsequent entry is ``(peak - value) / peak * 100``.
    """
    if not snapshots:
        return []
    peak = snapshots[0].total_value
    out: list[float] = []
    for s in snapshots:
        if s.total_value > peak:
            peak = s.total_value
        dd = 0.0 if peak <= 0 else (peak - s.total_value) / peak * 100.0
        out.append(round(dd, 4))
    return out


def take_snapshot(
    prices: dict[str, float],
    dir_path: Optional[str] = None,
) -> Snapshot:
    """Compute and append a mark-to-market snapshot.

    ``prices`` maps symbol -> latest close. The benchmark is recomputed
    as the equal-weighted buy-and-hold return of the assets in the
    benchmark basket since the first snapshot was recorded.
    """
    state = load_state(dir_path)
    snaps = load_snapshots(dir_path)
    bench = load_benchmark(dir_path)

    holdings_value = round(
        sum(
            pos.shares * float(prices.get(pos.symbol, pos.avg_cost))
            for pos in state.positions
        ),
        2,
    )
    total_value = round(state.cash + holdings_value, 2)

    # daily_return
    if snaps:
        prev_total = snaps[-1].total_value
        daily_ret = 0.0 if prev_total <= 0 else (total_value - prev_total) / prev_total * 100.0
    else:
        daily_ret = 0.0

    # total_return
    total_ret = 0.0 if state.initial_capital <= 0 else (
        (total_value - state.initial_capital) / state.initial_capital * 100.0
    )

    # Benchmark — initialise on first snapshot
    if not bench.get("init_prices") and state.positions:
        # Seed benchmark init_prices with current price for each held asset,
        # plus any monitor asset we can fetch.
        for sym, px in prices.items():
            bench.setdefault("init_prices", {})[sym] = float(px)
    # Always update benchmark init_prices for assets that don't have one yet
    # using the latest known close — only on the very first snapshot for that asset.
    for sym, px in prices.items():
        if sym not in bench.setdefault("init_prices", {}):
            bench["init_prices"][sym] = float(px)

    # Compute benchmark value: initial_capital * mean of (current/init) over the
    # set of assets that have init_prices. If a current price is missing, use
    # the last known close (passed in prices dict) or skip.
    init_prices: dict[str, float] = bench.get("init_prices", {})
    if init_prices:
        ratios: list[float] = []
        for sym, init_px in init_prices.items():
            cur_px = prices.get(sym)
            if cur_px is None or cur_px <= 0 or init_px <= 0:
                continue
            ratios.append(cur_px / init_px)
        if ratios:
            benchmark_value = round(state.initial_capital * (sum(ratios) / len(ratios)), 2)
        else:
            benchmark_value = state.initial_capital
    else:
        benchmark_value = state.initial_capital

    snap = Snapshot(
        timestamp=_now_iso(),
        cash=round(state.cash, 2),
        holdings_value=holdings_value,
        total_value=total_value,
        daily_return_pct=round(daily_ret, 4),
        total_return_pct=round(total_ret, 4),
        drawdown_pct=0.0,  # filled below
        benchmark_value=benchmark_value,
        prices=dict(prices),
    )
    snaps.append(snap)
    drawdowns = compute_drawdown(snaps)
    snap.drawdown_pct = drawdowns[-1]
    # Update cache on state
    state.max_drawdown_pct = max(drawdowns) if drawdowns else 0.0
    _save_state(state, dir_path)
    _write_json(_snap_path(dir_path), [s.to_dict() for s in snaps])
    # Persist benchmark init_prices + last value
    bench["snapshots"] = bench.get("snapshots", [])
    bench["snapshots"].append({
        "timestamp": snap.timestamp,
        "value": benchmark_value,
    })
    _write_json(_bench_path(dir_path), bench)
    return snap


# ── Convenience views ─────────────────────────────────────────────────────
def fetch_latest_prices(symbols: Iterable[str]) -> dict[str, float]:
    """Get the latest close for every symbol via the market service.

    Falls back silently to an empty mapping for symbols with no data —
    callers should use position.avg_cost as a worst-case substitute.
    """
    from ..services import market  # local import to avoid circular at import time
    out: dict[str, float] = {}
    for sym in symbols:
        try:
            info = market.latest_price(sym)
            price = info.get("price")
            if price is not None:
                out[sym] = float(price)
        except Exception:  # noqa: BLE001
            continue
    return out


def compute_holdings_value(
    state: PortfolioState, prices: dict[str, float]
) -> tuple[float, list[dict[str, Any]]]:
    """Return (total_holdings_value, per_position_rows).

    Each row is a dict: {symbol, shares, avg_cost, last_price, value, pnl, pnl_pct}.
    Missing prices fall back to avg_cost (so total_value still reconciles).
    """
    rows: list[dict[str, Any]] = []
    total = 0.0
    for pos in state.positions:
        last = float(prices.get(pos.symbol, pos.avg_cost))
        value = round(pos.shares * last, 2)
        pnl = round(value - pos.total_cost, 2)
        pnl_pct = 0.0 if pos.total_cost <= 0 else pnl / pos.total_cost * 100.0
        rows.append({
            "symbol": pos.symbol,
            "shares": pos.shares,
            "avg_cost": pos.avg_cost,
            "last_price": round(last, 4),
            "value": value,
            "pnl": pnl,
            "pnl_pct": round(pnl_pct, 2),
        })
        total += value
    return round(total, 2), rows


def compute_unrealised_pnl(
    state: PortfolioState, prices: dict[str, float]
) -> dict[str, float]:
    """Aggregate unrealised P&L across every open position."""
    holdings, rows = compute_holdings_value(state, prices)
    total_cost = sum(r["value"] - r["pnl"] for r in rows)  # = sum(pos.total_cost)
    total_pnl = sum(r["pnl"] for r in rows)
    return {
        "holdings_value": holdings,
        "total_cost": round(total_cost, 2),
        "unrealised_pnl": round(total_pnl, 2),
        "unrealised_pnl_pct": (
            0.0 if total_cost <= 0 else round(total_pnl / total_cost * 100.0, 4)
        ),
    }


# ── CSV export ────────────────────────────────────────────────────────────
def snapshots_to_csv(snapshots: list[Snapshot]) -> str:
    """Render the snapshot series as a CSV string for external graphing."""
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "timestamp", "cash", "holdings_value", "total_value",
        "daily_return_pct", "total_return_pct", "drawdown_pct",
        "benchmark_value",
    ])
    for s in snapshots:
        writer.writerow([
            s.timestamp, s.cash, s.holdings_value, s.total_value,
            s.daily_return_pct, s.total_return_pct, s.drawdown_pct,
            s.benchmark_value,
        ])
    return buf.getvalue()
