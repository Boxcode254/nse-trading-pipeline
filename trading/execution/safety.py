"""Safety Engine — risk management layer."""
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from .models import OrderRequest, OrderResult, SafetyVerdict, AccountInfo


class SafetyEngine:
    """Risk management layer that enforces trading limits."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with config defaults + state persistence."""
        # Config defaults
        self.config = {
            "max_trade_size_kes": 500_000.0,
            "max_daily_loss_kes": 100_000.0,    # tracks actual realised losses
            "max_daily_loss_pct": 100.0,        # effectively unlimited for paper
            "max_single_exposure_pct": 25.0,
            "max_position_count": 20,
            "enabled": True,
            "emergency_stop_path": os.path.expanduser(
                "~/.trading/execution/EMERGENCY_STOP"
            ),
        }
        if config:
            self.config.update(config)

        # Runtime state
        self.state_dir = Path(self.config.get("state_dir", os.path.expanduser("~/.trading/execution")))
        self.state = self._load_state()

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

    def get_status(self) -> dict:
        """Return full safety status."""
        return {
            "emergency_stop": self.state["emergency_stop"],
            "daily_realised_pnl": self.state["daily_realised_pnl"],
            "daily_trade_count": self.state["daily_trade_count"],
            "daily_gross_loss": self.state["daily_gross_loss"],
            "last_reset_date": self.state["last_reset_date"],
            "manual_overrides": dict(self.state["manual_overrides"]),
            "config": dict(self.config),
        }

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
            }
        with open(state_path, "r") as f:
            return json.load(f)

    def _save_state(self) -> None:
        """Persist state to disk."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_path = self.state_dir / "safety_state.json"
        with open(state_path, "w") as f:
            json.dump(self.state, f, indent=2)