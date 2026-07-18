"""Paper trading broker implementation."""
from typing import Optional
import os
from pathlib import Path

from ..broker import BrokerBase
from ..models import OrderRequest, OrderResult, AccountInfo, BrokerPosition
from ...portfolio.engine import (
    load_state,
    buy as pf_buy,
    sell as pf_sell,
    Position as PfPosition,
)


class PaperBroker(BrokerBase):
    """Paper trading broker that wraps the portfolio engine."""
    name = "paper"
    connected = False

    def __init__(self, portfolio_dir: Optional[str] = None):
        self.portfolio_dir = portfolio_dir or os.path.expanduser("~/.trading/portfolio")

    def connect(self) -> bool:
        """Paper broker is always connected."""
        self.connected = True
        return True

    def disconnect(self) -> bool:
        """Paper broker is always connected."""
        self.connected = True
        return True

    def is_connected(self) -> bool:
        return self.connected

    def get_account(self) -> AccountInfo:
        state = load_state(self.portfolio_dir)
        return AccountInfo(
            cash=state.cash,
            equity=state.cash + sum(p.total_cost for p in state.positions),
            buying_power=state.cash,
            positions_count=len(state.positions),
            daily_pnl=0.0,
            daily_pnl_pct=0.0,
            currency="KES",
            broker="paper",
        )

    def get_positions(self) -> list[BrokerPosition]:
        state = load_state(self.portfolio_dir)
        return [
            BrokerPosition(
                symbol=p.symbol,
                quantity=p.shares,
                market_value=p.total_cost,
                cost_basis=p.avg_cost,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
            )
            for p in state.positions
        ]

    def place_order(self, request: OrderRequest) -> OrderResult:
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

        return OrderResult(
            success=True,
            order_id=str(hash(txn.timestamp)),
            symbol=txn.symbol,
            side=txn.action,
            quantity=txn.shares,
            price=txn.price,
            total=txn.total,
            fee=txn.fee,
            status="filled",
            message="",
            timestamp=txn.timestamp,
            external_id="",
            realised_pnl=txn.realised_pnl,
        )

    def get_order_status(self, order_id: str) -> OrderResult:
        raise NotImplementedError("Paper broker does not track order status")

    def cancel_order(self, order_id: str) -> bool:
        return False

    def get_price(self, symbol: str) -> float:
        from ...portfolio.engine import fetch_latest_prices
        prices = fetch_latest_prices([symbol])
        return prices.get(symbol, 0.0)