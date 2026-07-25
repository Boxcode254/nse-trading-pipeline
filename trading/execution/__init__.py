"""Execution Engine package — safety layer + broker abstraction.

Exports:
- ExecutionEngine: orchestrates safety checks + broker execution
- SafetyEngine: risk management + position limits
- BrokerBase: abstract interface all brokers implement
- OrderRequest, OrderResult, SafetyVerdict, ExecutionReport: data models
- OrderStatus: order lifecycle state machine
- OrderStore: persistent, validated order book
- CircuitBreaker: persisted broker-failure breaker
- PaperBroker, MetaTraderBroker, InteractiveBrokersBroker, OandaBroker: implementations
"""

from .models import (
    OrderRequest,
    OrderResult,
    SafetyVerdict,
    ExecutionReport,
    AccountInfo,
    BrokerPosition,
    OrderStatus,
    is_terminal,
    is_open,
)
from .broker import BrokerBase
from .brokers import (
    PaperBroker,
    MetaTraderBroker,
    InteractiveBrokersBroker,
    OandaBroker,
)
from .safety import SafetyEngine
from .engine import ExecutionEngine
from .order_store import OrderStore
from .circuit_breaker import CircuitBreaker
from .retry import call_with_timeout, with_exponential_backoff, execute_resilient
from .alerting import alert, log_alert, send_telegram
from .run_lock import RunLock

__all__ = [
    "ExecutionEngine",
    "SafetyEngine",
    "BrokerBase",
    "OrderRequest",
    "OrderResult",
    "SafetyVerdict",
    "ExecutionReport",
    "AccountInfo",
    "BrokerPosition",
    "OrderStatus",
    "is_terminal",
    "is_open",
    "OrderStore",
    "CircuitBreaker",
    "RunLock",
    "call_with_timeout",
    "with_exponential_backoff",
    "execute_resilient",
    "alert",
    "log_alert",
    "send_telegram",
    "PaperBroker",
    "MetaTraderBroker",
    "InteractiveBrokersBroker",
    "OandaBroker",
]