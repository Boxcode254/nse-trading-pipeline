"""Safety Engine — risk management layer.

Phase 1 risk-gate additions (2026-07-25):
- **Portfolio drawdown halt** (MTM equity-curve check). When the portfolio's
  peak-to-current drawdown exceeds ``max_drawdown_halt_pct``, ALL new trades
  are blocked until an operator releases the halt. This is the "risk gate"
  the auto-trader and manual CLI now share.
- **Stop-loss moved INTO the gate.** ``should_stop_loss()`` computes the
  loss% for a held position and the gate's ``check_order`` will BLOCK a BUY
  that would add to a losing position already past the stop, and FLAG (not
  block) any SELL. The auto-trader reuses the same helper so the logic is
  single-sourced.
- **Macro / volatility circuit breaker.** A ``MacroBreaker`` evaluates the
  NSE index / breadth / vol regime. When tripped, the gate blocks all trades.
  TradingView fetch is best-effort and non-fatal (fail-open by design).
"""
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from .models import OrderRequest, OrderResult, SafetyVerdict, AccountInfo
from .macro_breaker import MacroBreaker, DEFAULT_THRESHOLDS as _MACRO_DEFAULTS
from .retry import call_with_timeout

# Single source of truth: when available, the SafetyEngine's DEFAULTS come from
# trading.config.EXECUTION_CONFIG so stop_loss_pct / take_profit_pct / etc. can
# never drift from the one place they're tuned. The literal below is only a
# fallback for isolated imports (e.g. unit tests that don't load the trading
# package) and MUST stay in sync with EXECUTION_CONFIG for those cases.
try:
    from trading import config as _trading_config
    _SAFETY_DEFAULTS = dict(_trading_config.EXECUTION_CONFIG)
except Exception:  # pragma: no cover - import isolation guard
    _SAFETY_DEFAULTS = None


def _default_config() -> dict:
    """Return the engine's default config, sourced from EXECUTION_CONFIG."""
    # Keep safety-only defaults here even when EXECUTION_CONFIG is available:
    # the execution config intentionally omits the filesystem paths used by
    # the safety gate.  Merge the shared config over the complete fallback so
    # shared thresholds win while required path defaults survive.
    fallback = {
        "max_trade_size_kes": 500_000.0,
        "max_daily_loss_kes": 100_000.0,
        "max_daily_loss_pct": 100.0,
        "max_single_exposure_pct": 25.0,
        "max_position_count": 20,
        "enabled": True,
        "emergency_stop_path": os.path.expanduser("~/.trading/execution/EMERGENCY_STOP"),
        "max_drawdown_halt_pct": 15.0,
        "stop_loss_pct": 8.0,
        "take_profit_pct": 20.0,
        "macro": dict(_MACRO_DEFAULTS),
        "macro_fail_open": True,
        "macro_state_path": os.path.expanduser("~/.trading/execution/macro_breaker.json"),
    }
    if _SAFETY_DEFAULTS is not None:
        fallback.update(_SAFETY_DEFAULTS)
    return fallback

# Hard cap on the macro price scan so a slow upstream can never stall the run.
_MACRO_FETCH_TIMEOUT = 15.0


class SafetyEngine:
    """Risk management layer that enforces trading limits."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with config defaults + state persistence.

        Defaults come from ``_default_config()`` (trading.config.EXECUTION_CONFIG),
        the single source of truth), so stop_loss_pct / take_profit_pct / etc.
        always match what's tuned in one place. An explicit ``config`` dict is
        merged on top (used by tests to override).
        """
        self.config = _default_config()
        if config:
            self.config.update(config)

        # Runtime state
        self.state_dir = Path(self.config.get("state_dir", os.path.expanduser("~/.trading/execution")))
        self.state = self._load_state()

        # Macro breaker (fail-open unless explicitly configured otherwise).
        try:
            self.macro = MacroBreaker(
                thresholds=self.config.get("macro", {}),
                state_path=self.config.get("macro_state_path", os.path.expanduser(
                    "~/.trading/execution/macro_breaker.json"
                )),
                fail_open=self.config.get("macro_fail_open", True),
            )
        except Exception:
            # Never let a broken macro breaker disable the whole gate.
            self.macro = None

    def check_order(
        self, request: OrderRequest, portfolio_state: dict, account: AccountInfo
    ) -> SafetyVerdict:
        """Run ALL safety checks before a trade."""
        violations = []

        # 1. Emergency stop check
        if self._is_emergency_stop_active():
            violations.append("emergency_stop_active")
            return SafetyVerdict(
                allowed=False,
                reason="Trading halted by emergency stop",
                violations=violations,
            )

        # 1b. Portfolio drawdown halt (MTM equity-curve check)
        #     When the live peak-to-current drawdown exceeds the configured
        #     threshold, ALL new orders are blocked. This is a portfolio-level
        #     circuit breaker that covers both manual CLI trades and the
        #     auto-trader, because both route through check_order().
        if self._drawdown_halt_active():
            violations.append("drawdown_halt")
            return SafetyVerdict(
                allowed=False,
                reason=(
                    f"Portfolio drawdown halt: {self.state['drawdown_pct']:.2f}% "
                    f"exceeds {self.config['max_drawdown_halt_pct']:.2f}% limit — "
                    f"trading paused until operator release"
                ),
                violations=violations,
            )

        # 1c. Macro / volatility circuit breaker
        #     If the NSE index / breadth / vol regime has tripped, halt all
        #     trades. Fail-open: a missing/errored macro feed does NOT trip.
        if self.macro is not None and self.macro.evaluate():
            violations.append("macro_breaker")
            return SafetyVerdict(
                allowed=False,
                reason=f"Macro circuit breaker tripped: {self.macro.snapshot().get('reason', 'market stress')}",
                violations=violations,
            )

        # 2. Manual override check
        if request.symbol in self.state["manual_overrides"]:
            action = self.state["manual_overrides"][request.symbol]
            if action == "block":
                violations.append("manual_block")
                return SafetyVerdict(
                    allowed=False,
                    reason=f"Manual block active for {request.symbol}",
                    violations=violations,
                )

        # 3. Max trade size check
        total = request.quantity * (request.price or 0.0)
        if total > self.config["max_trade_size_kes"]:
            violations.append("max_trade_size")

        # 4. Max daily loss check — realised losses only.
        #    Buy notional is NOT a loss; counting it as one blocks normal
        #    deployment under any realistic max_daily_loss_pct (e.g. 5%).
        loss_so_far = (
            abs(min(0, self.state["daily_realised_pnl"]))
            + self.state["daily_gross_loss"]
        )
        if loss_so_far > self.config["max_daily_loss_kes"]:
            violations.append("max_daily_loss_kes")
        if account.equity > 0 and (
            loss_so_far / account.equity * 100 > self.config["max_daily_loss_pct"]
        ):
            violations.append("max_daily_loss_pct")

        # 5. Max single exposure check (BUY only)
        if request.side == "BUY":
            new_position_value = (
                portfolio_state.get("positions", {})
                .get(request.symbol, {})
                .get("value", 0.0)
                + total
            )
            if new_position_value / account.equity * 100 > self.config[
                "max_single_exposure_pct"
            ]:
                violations.append("max_single_exposure")

            # 5b. Stop-loss is IN the gate. A BUY that adds to a position
            #     already past the stop-loss threshold is blocked — we do not
            #     average down into a losing name. SELLs are never blocked by
            #     the stop (selling is the correct response); they are reported
            #     separately via should_stop_loss() so the auto-trader can flag.
            sl = self.should_stop_loss(request.symbol, portfolio_state)
            if sl is not None and sl.get("stopped"):
                if request.side == "BUY":
                    violations.append("stop_loss_blocked")

        # 5c. Stop-loss SELL flag (informational, does NOT block).
        #     Record so the caller's report can show the stop context. This is
        #     computed for SELLs too; it never adds a violation for SELL.
        if request.side == "SELL":
            sl = self.should_stop_loss(request.symbol, portfolio_state)
            if sl is not None and sl.get("stopped"):
                # Marker only — SELLs through the stop are allowed.
                pass

        # 6. Max position count (BUY only)
        if request.side == "BUY" and len(
            portfolio_state.get("positions", {})
        ) >= self.config["max_position_count"]:
            violations.append("max_position_count")

        # 7. Check if enabled
        if not self.config["enabled"]:
            return SafetyVerdict(allowed=True, reason="Safety checks disabled")

        return SafetyVerdict(
            allowed=not bool(violations),
            reason="Violations: " + ", ".join(violations) if violations else "",
            violations=violations,
        )

    def record_trade(self, result: OrderResult) -> None:
        """Update daily counters after a completed trade."""
        if result.side == "SELL" and result.realised_pnl is not None:
            if result.realised_pnl < 0:
                self.state["daily_gross_loss"] += abs(result.realised_pnl)
        self.state["daily_trade_count"] += 1
        self._save_state()

    def emergency_stop(self) -> None:
        """Activate the global kill switch."""
        self.state["emergency_stop"] = True
        stop_path = Path(self.config["emergency_stop_path"])
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        stop_path.touch()
        self._save_state()

    def release_emergency_stop(self) -> None:
        """Release the kill switch."""
        self.state["emergency_stop"] = False
        try:
            Path(self.config["emergency_stop_path"]).unlink()
        except FileNotFoundError:
            pass
        self._save_state()

    def set_manual_override(self, symbol: str, action: str) -> None:
        """Block or force trades for a specific symbol."""
        if action not in ("block", "force"):
            raise ValueError("action must be 'block' or 'force'")
        self.state["manual_overrides"][symbol] = action
        self._save_state()

    def clear_manual_override(self, symbol: str) -> None:
        """Remove manual override for a symbol."""
        self.state["manual_overrides"].pop(symbol, None)
        self._save_state()

    # ── Phase 1 helpers ───────────────────────────────────────────────
    def should_take_profit(
        self, symbol: str, portfolio_state: dict
    ) -> Optional[dict]:
        """Return take-profit status for a held symbol, or None if not held / disabled.

        Mirror of :meth:`should_stop_loss` but for winners: reads the position's
        avg cost and current value from ``portfolio_state`` (same dict the gate
        uses) so this is a single source of truth shared by the auto-trader and
        the manual CLI. When the gain vs avg cost reaches ``take_profit_pct``
        the position is fully exited ("sell the winner and leave").

        Returns a dict::
            {"symbol", "taken": bool, "gain_pct": float,
             "avg_cost": float, "current_price": float}
        or ``None`` when the position is absent or take-profit is disabled
        (``take_profit_pct`` is 0/None).
        """
        pct_cfg = self.config.get("take_profit_pct") or 0.0
        if pct_cfg <= 0:
            return None
        pos = portfolio_state.get("positions", {}).get(symbol)
        if not pos:
            return None
        avg_cost = pos.get("avg_cost")
        shares = pos.get("shares") or 0
        value = pos.get("value", 0.0) or 0.0
        if not avg_cost or avg_cost <= 0 or shares <= 0:
            # Fallback: no avg cost basis → cannot compute a gain%.
            return None
        current_price = value / shares if value else avg_cost
        gain_pct = (current_price - avg_cost) / avg_cost * 100.0
        return {
            "symbol": symbol,
            "taken": gain_pct >= pct_cfg,
            "gain_pct": round(gain_pct, 2),
            "avg_cost": round(float(avg_cost), 4),
            "current_price": round(float(current_price), 4),
        }


    def should_stop_loss(
        self, symbol: str, portfolio_state: dict
    ) -> Optional[dict]:
        """Return stop-loss status for a held symbol, or None if not held / disabled.

        Reads the position's avg cost and current value from ``portfolio_state``
        (the same dict the gate uses), so this is a single source of truth shared
        by the auto-trader and the manual CLI.

        Returns a dict::
            {"symbol", "stopped": bool, "loss_pct": float,
             "avg_cost": float, "current_price": float}
        or ``None`` when the position is absent or stop-loss is disabled
        (``stop_loss_pct`` is 0/None).
        """
        pct_cfg = self.config.get("stop_loss_pct") or 0.0
        if pct_cfg <= 0:
            return None
        pos = portfolio_state.get("positions", {}).get(symbol)
        if not pos:
            return None
        avg_cost = pos.get("avg_cost")
        # current_price is derived from value/shares if present; else value
        # itself is treated as the notional given the shares.
        shares = pos.get("shares") or 0
        value = pos.get("value", 0.0) or 0.0
        if not avg_cost or avg_cost <= 0 or shares <= 0:
            # Fallback: no avg cost basis → cannot compute a loss%.
            return None
        current_price = value / shares if value else avg_cost
        loss_pct = (current_price - avg_cost) / avg_cost * 100.0
        return {
            "symbol": symbol,
            "stopped": loss_pct <= -pct_cfg,
            "loss_pct": round(loss_pct, 2),
            "avg_cost": round(float(avg_cost), 4),
            "current_price": round(float(current_price), 4),
        }

    def update_drawdown(self, drawdown_pct: float, *, halt_if_exceeds: bool = True) -> dict:
        """Record the current portfolio drawdown % from the MTM equity curve.

        When ``drawdown_pct`` exceeds ``max_drawdown_halt_pct`` the halt is
        engaged (idempotent — once halted it stays until ``release_drawdown_halt``).
        Returns a status dict describing the resulting halt state.
        """
        self.state["drawdown_pct"] = round(float(drawdown_pct), 4)
        if halt_if_exceeds:
            limit = self.config.get("max_drawdown_halt_pct") or 0.0
            if limit > 0 and drawdown_pct >= limit:
                if not self.state.get("drawdown_halted"):
                    self.state["drawdown_halted"] = True
                    self.state["drawdown_halt_reason"] = (
                        f"Drawdown {drawdown_pct:.2f}% hit {limit:.2f}% halt threshold"
                    )
        self._save_state()
        return {
            "drawdown_pct": self.state["drawdown_pct"],
            "halted": self.state["drawdown_halted"],
            "reason": self.state["drawdown_halt_reason"],
            "limit": self.config.get("max_drawdown_halt_pct", 0.0),
        }

    def release_drawdown_halt(self) -> None:
        """Operator acknowledgement — clear the drawdown halt (does not reset the %)."""
        self.state["drawdown_halted"] = False
        self.state["drawdown_halt_reason"] = ""
        self._save_state()

    def _drawdown_halt_active(self) -> bool:
        """True when the portfolio drawdown halt is engaged."""
        return bool(self.state.get("drawdown_halted"))

    def get_status(self) -> dict:
        """Return full safety status."""
        status = {
            "emergency_stop": self.state["emergency_stop"],
            "daily_realised_pnl": self.state["daily_realised_pnl"],
            "daily_trade_count": self.state["daily_trade_count"],
            "daily_gross_loss": self.state["daily_gross_loss"],
            "last_reset_date": self.state["last_reset_date"],
            "manual_overrides": dict(self.state["manual_overrides"]),
            "config": dict(self.config),
            # Phase 1 — drawdown halt
            "drawdown_pct": self.state.get("drawdown_pct", 0.0),
            "drawdown_halted": self.state.get("drawdown_halted", False),
            "drawdown_halt_reason": self.state.get("drawdown_halt_reason", ""),
            "drawdown_halt_limit": self.config.get("max_drawdown_halt_pct", 0.0),
        }
        if self.macro is not None:
            status["macro_breaker"] = self.macro.snapshot()
        return status

    def feed_macro(self, snapshot_dict: dict) -> dict:
        """Feed a macro snapshot (dict) into the breaker. Returns its status.

        ``snapshot_dict`` keys: timestamp, index_level, index_change_pct,
        advancers, decliners, volatility_pct, source. Any subset is accepted.
        """
        if self.macro is None:
            return {"tripped": False, "reason": "macro breaker unavailable",
                    "breach": None, "evaluated": False}
        from .macro_breaker import MacroSnapshot
        snap = MacroSnapshot(
            timestamp=snapshot_dict.get("timestamp") or datetime.now().isoformat(),
            index_level=snapshot_dict.get("index_level"),
            index_change_pct=snapshot_dict.get("index_change_pct"),
            advancers=snapshot_dict.get("advancers"),
            decliners=snapshot_dict.get("decliners"),
            volatility_pct=snapshot_dict.get("volatility_pct"),
            source=snapshot_dict.get("source", "feed"),
        )
        return self.macro.feed(snap)

    def release_macro(self) -> None:
        """Manual release of the macro circuit breaker."""
        if self.macro is not None:
            self.macro.reset()

    def refresh_macro(self, *, prices: Optional[dict] = None, min_sample: int = 5) -> dict:
        """Generate a macro snapshot from live prices and feed the breaker.

        Convenience wrapper used by the auto-trader and the morning cron: it
        pulls the current watchlist prices (or accepts a pre-fetched map) and
        derives breadth / composite-index-change / dispersion, then feeds the
        breaker so the trade gate halts on a real market-regime signal.

        ``min_sample`` is forwarded to the snapshot builder: thresholds are
        only evaluated when at least that many symbols actually MOVED
        (non-flat). Below it, the breaker stays fail-open (never trips on a
        sparse/flat feed).

        Returns the breaker status dict. Failures are non-fatal — if price
        fetch fails the breaker simply stays in its current (fail-open) state.
        """
        if self.macro is None:
            return {"tripped": False, "reason": "macro breaker unavailable",
                    "breach": None, "evaluated": False}
        if prices is None:
            try:
                from trading.nse_price_fetcher import fetch_prices

                # Bounded scan: even if the per-symbol timeout in fetch_prices
                # were bypassed, the macro refresh can never stall the run
                # longer than _MACRO_FETCH_TIMEOUT (fail-open on timeout).
                completed, result, err = call_with_timeout(
                    fetch_prices, _MACRO_FETCH_TIMEOUT
                )
                prices = result if completed and result is not None else {}
                if not completed:
                    # Leave the breaker in its current (fail-open) state.
                    pass
            except Exception:
                prices = {}
        return self.macro.build_snapshot_from_prices(prices, min_sample=min_sample)

    def reset_daily(self) -> None:
        """Reset daily counters."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state["last_reset_date"] == today:
            return
        self.state["daily_realised_pnl"] = 0.0
        self.state["daily_trade_count"] = 0
        self.state["daily_gross_loss"] = 0.0
        self.state["last_reset_date"] = today
        self._save_state()

    def _is_emergency_stop_active(self) -> bool:
        """Check both in-memory flag and file-based kill switch."""
        return self.state["emergency_stop"] or Path(
            self.config["emergency_stop_path"]
        ).exists()

    def _load_state(self) -> dict:
        """Load state from disk or return defaults."""
        state_path = self.state_dir / "safety_state.json"
        if not state_path.exists():
            return {
                "daily_realised_pnl": 0.0,
                "daily_trade_count": 0,
                "daily_gross_loss": 0.0,
                "last_reset_date": datetime.now().strftime("%Y-%m-%d"),
                "emergency_stop": False,
                "manual_overrides": {},
                # Phase 1 — drawdown halt state
                "drawdown_pct": 0.0,
                "drawdown_halted": False,
                "drawdown_halt_reason": "",
            }
        with open(state_path, "r") as f:
            data = json.load(f)
        # Backfill any missing Phase 1 keys so older state files still load.
        data.setdefault("drawdown_pct", 0.0)
        data.setdefault("drawdown_halted", False)
        data.setdefault("drawdown_halt_reason", "")
        return data

    def _save_state(self) -> None:
        """Persist state to disk."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_path = self.state_dir / "safety_state.json"
        with open(state_path, "w") as f:
            json.dump(self.state, f, indent=2)