"""OANDA broker stub."""
from typing import Optional

from ..broker import BrokerBase
from ..models import OrderRequest, OrderResult, AccountInfo, BrokerPosition


class OandaBroker(BrokerBase):
    """OANDA broker stub (ready for connector integration)."""
    name = "oanda"
    connected = False

    def connect(self) -> bool:
        """Stub - always returns True."""
        self.connected = True
        return True

    def disconnect(self) -> bool:
        """Stub - always returns True."""
        self.connected = False
        return True

    def is_connected(self) -> bool:
        return self.connected

    def get_account(self) -> AccountInfo:
        return AccountInfo(
            cash=0.0,
            equity=0.0,
            buying_power=0.0,
            positions_count=0,
            daily_pnl=0.0,
            daily_pnl_pct=0.0,
            currency="KES",
            broker="oanda",
        )

    def get_positions(self) -> list[BrokerPosition]:
        return []

    def place_order(self, request: OrderRequest) -> OrderResult:
        raise NotImplementedError("OANDA integration not implemented")

    def get_order_status(self, order_id: str) -> OrderResult:
        raise NotImplementedError("OANDA integration not implemented")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("OANDA integration not implemented")

    def get_price(self, symbol: str) -> float:
        return 0.0