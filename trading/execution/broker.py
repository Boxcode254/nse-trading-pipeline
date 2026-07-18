"""Broker base class and interface definition."""
from abc import ABC, abstractmethod
from typing import Optional

from .models import OrderRequest, OrderResult, AccountInfo, BrokerPosition


class BrokerBase(ABC):
    """Abstract base class defining the broker interface."""
    name: str = "base"
    connected: bool = False

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the broker. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> bool:
        """Disconnect from the broker. Returns True on success."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """True if currently connected to the broker."""
        ...

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get current account balance and status."""
        ...

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]:
        """List all open positions."""
        ...

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        """Execute a trade order."""
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        """Check status of an existing order."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Attempt to cancel an open order."""
        ...

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """Get latest price for a symbol."""
        ...