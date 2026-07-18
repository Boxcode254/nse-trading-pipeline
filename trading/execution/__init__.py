"""Execution Engine package — safety layer + broker abstraction.

Exports:
- ExecutionEngine: orchestrates safety checks + broker execution
- SafetyEngine: risk management + position limits
- BrokerBase: abstract interface all brokers implement
- OrderRequest, OrderResult, SafetyVerdict, ExecutionReport: data models
- PaperBroker, MetaTraderBroker, InteractiveBrokersBroker, OandaBroker: implementations
"""

from .models import (
    OrderRequest,
    OrderResult,
    SafetyVerdict,
    ExecutionReport,
    AccountInfo,
    BrokerPosition,
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
    "PaperBroker",
    "MetaTraderBroker",
    "InteractiveBrokersBroker",
    "OandaBroker",
]