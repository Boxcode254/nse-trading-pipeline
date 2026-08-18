"""Paper trading broker implementation.

Wraps the portfolio engine for fills, but now also maintains an in-broker
order record keyed by a **uuid order_id** and tracks ``client_order_id`` so
the execution engine's idempotency + reconciliation layers have a real
``get_order_status`` / ``cancel_order`` to call.

Paper fills are synchronous and always complete at the requested price, so:
- ``get_order_status`` returns FILLED for any order we recorded.
- ``cancel_order`` returns False for fills (already executed) — paper trades
  cannot be cancelled, which is correct: the engine should not *think* it
  cancelled a fill that already settled.
"""
from typing import Optional
import os
import uuid
from pathlib import Path

from ..broker import BrokerBase
from ..models import (
    OrderRequest, OrderResult, AccountInfo, BrokerPosition, OrderStatus,
)

# In-broker order ledger: order_id -> record. Persisted to disk so a process
# restart can still answer get_order_status. Lives under the portfolio dir's
# parent so it is colocated with the paper portfolio.
_LEDGER_NAME = "paper_orders.json"


class PaperBroker(BrokerBase):
    """Paper trading broker that wraps the portfolio engine."""
    name = "paper"
    connected = False

    def __init__(self, portfolio_dir: Optional[str] = None):
        self.portfolio_dir = portfolio_dir or os.path.expanduser("~/.trading/portfolio")
        self._ledger_path = Path(self.portfolio_dir).parent / _LEDGER_NAME

    # ── Persistence of the paper order ledger ──
    def _load_ledger(self) -> dict:
        if self._ledger_path.exists():
            try:
                return __import__("json").loads(self._ledger_path.read_text())
            except Exception:
                return {}
        return {}

    def _save_ledger(self, ledger: dict) -> None:
        try:
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ledger_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                __import__("json").dump(ledger, f, indent=2)
            os.replace(tmp, self._ledger_path)
        except OSError:
            pass

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> bool:
        self.connected = True
        return True

    def is_connected(self) -> bool:
        return self.connected

    def get_account(self) -> AccountInfo:
        from ...portfolio.engine import load_state
        state = load_state(self.portfolio_dir)
        try:
            from ...price_source import resolve_prices
            prices = resolve_prices(
                [p.symbol for p in state.positions], self.portfolio_dir
            ).prices
            position_value = sum(
                p.shares * prices.get(
                    p.symbol, p.total_cost / p.shares if p.shares else 0.0
                )
                for p in state.positions
            )
        except Exception:
            position_value = sum(p.total_cost for p in state.positions)
        return AccountInfo(
            cash=state.cash,
            equity=state.cash + position_value,
            buying_power=state.cash,
            positions_count=len(state.positions),
            daily_pnl=0.0,
            daily_pnl_pct=0.0,
            currency="KES",
            broker="paper",
        )

    def get_positions(self) -> list[BrokerPosition]:
        from ...portfolio.engine import load_state
        state = load_state(self.portfolio_dir)
        try:
            from ...price_source import resolve_prices
            prices = resolve_prices(
                [p.symbol for p in state.positions], self.portfolio_dir
            ).prices
        except Exception:
            prices = {}
        return [
            BrokerPosition(
                symbol=p.symbol,
                quantity=p.shares,
                market_value=round(
                    p.shares * prices.get(
                        p.symbol, p.total_cost / p.shares if p.shares else 0.0
                    ),
                    2,
                ),
                cost_basis=p.avg_cost,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
            )
            for p in state.positions
        ]

    def place_order(self, request: OrderRequest) -> OrderResult:
        from ...portfolio.engine import (
            load_state,
            buy as pf_buy,
            sell as pf_sell,
            Position as PfPosition,
        )

        if request.side == "BUY":
            _, txn = pf_buy(
                symbol=request.symbol,
                shares=request.quantity,
                price=request.price or 0.0,
                reason=request.reason,
                signal_ref=request.signal_ref,
                dir_path=self.portfolio_dir,
            )
        elif request.side == "SELL":
            _, txn = pf_sell(
                symbol=request.symbol,
                shares=request.quantity,
                price=request.price or 0.0,
                reason=request.reason,
                signal_ref=request.signal_ref,
                dir_path=self.portfolio_dir,
            )
        else:
            raise ValueError(f"Invalid side: {request.side}")

        order_id = str(uuid.uuid4())
        result = OrderResult(
            success=True,
            order_id=order_id,
            symbol=txn.symbol,
            side=txn.action,
            quantity=txn.shares,
            price=txn.price,
            total=txn.total,
            fee=txn.fee,
            status=OrderStatus.FILLED.value,
            message="",
            timestamp=txn.timestamp,
            external_id="",
            realised_pnl=txn.realised_pnl,
            filled_quantity=txn.shares,
            filled_price=txn.price,
            average_fill_price=txn.price,
            client_order_id=request.client_order_id,
        )
        # Record in the paper ledger for get_order_status.
        ledger = self._load_ledger()
        ledger[order_id] = {
            "client_order_id": request.client_order_id,
            "symbol": txn.symbol,
            "side": txn.action,
            "quantity": txn.shares,
            "price": txn.price,
            "filled_quantity": txn.shares,
            "filled_price": txn.price,
            "average_fill_price": txn.price,
            "status": OrderStatus.FILLED.value,
        }
        self._save_ledger(ledger)
        return result

    def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        """Real status lookup for a paper order by broker order_id."""
        ledger = self._load_ledger()
        rec = ledger.get(order_id)
        if rec is None:
            return None
        return OrderResult(
            success=True,
            order_id=order_id,
            symbol=rec["symbol"],
            side=rec["side"],
            quantity=rec["quantity"],
            price=rec.get("price", 0.0),
            total=rec.get("price", 0.0) * rec["quantity"],
            fee=0.0,
            status=rec.get("status", OrderStatus.FILLED.value),
            message="",
            timestamp="",
            external_id="",
            filled_quantity=rec.get("filled_quantity", rec["quantity"]),
            filled_price=rec.get("filled_price"),
            average_fill_price=rec.get("average_fill_price"),
            client_order_id=rec.get("client_order_id"),
        )

    def cancel_order(self, order_id: str) -> bool:
        # Paper fills are synchronous and already settled — cannot cancel.
        return False

    def get_price(self, symbol: str) -> float:
        from ...portfolio.engine import fetch_latest_prices
        prices = fetch_latest_prices([symbol])
        return prices.get(symbol, 0.0)
