"""Execution Engine — orchestrates safety checks + broker execution."""
from dataclasses import asdict
from typing import Optional, Dict, Any

from .models import OrderRequest, ExecutionReport, AccountInfo
from .broker import BrokerBase
from .safety import SafetyEngine


class ExecutionEngine:
    """Orchestrates the full execution pipeline."""

    def __init__(self, broker: BrokerBase, safety: Optional[SafetyEngine] = None):
        self.broker = broker
        self.safety = safety or SafetyEngine()
        self._connected = False

    def connect(self) -> bool:
        """Connect to the broker."""
        self._connected = self.broker.connect()
        return self._connected

    def disconnect(self) -> bool:
        """Disconnect from the broker."""
        ok = self.broker.disconnect()
        self._connected = False
        return ok

    def execute(
        self,
        request: OrderRequest,
        portfolio_state: Optional[Dict[str, Any]] = None,
        account: Optional[AccountInfo] = None,
    ) -> ExecutionReport:
        """Execute an order through the full safety-checked pipeline."""
        # Auto-connect if not connected
        if not self._connected:
            self.connect()

        # Get portfolio context for safety checks
        if portfolio_state is None:
            portfolio_state = self._get_portfolio_state()

        if account is None:
            account = self.broker.get_account()

        # Safety gate — NEVER bypassed
        verdict = self.safety.check_order(request, portfolio_state, account)
        if not verdict.allowed:
            return ExecutionReport(
                success=False, safety=verdict, message=verdict.reason
            )

        # Execute
        try:
            result = self.broker.place_order(request)
        except Exception as e:
            return ExecutionReport(
                success=False, message=f"Broker error: {e}"
            )

        # Record
        if result.success:
            self.safety.record_trade(result)

        return ExecutionReport(
            success=result.success,
            order=result,
            safety=verdict,
            message=result.message,
        )

    def get_status(self) -> Dict[str, Any]:
        """Full execution engine status."""
        account = self.broker.get_account() if self._connected else None
        return {
            "connected": self._connected,
            "broker": self.broker.name,
            "account": asdict(account) if account else None,
            "safety": self.safety.get_status(),
        }

    def _get_portfolio_state(self) -> Dict[str, Any]:
        """Build portfolio state dict from the broker."""
        try:
            account = self.broker.get_account()
            positions = self.broker.get_positions()
            return {
                "cash": account.cash,
                "positions": {
                    p.symbol: {"shares": p.quantity, "value": p.market_value}
                    for p in positions
                },
                "total_value": account.equity,
            }
        except Exception:
            return {}