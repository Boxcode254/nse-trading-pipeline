"""Trading Learning Package - Decision Journal for Paper Trading."""

from .db import (
    get_conn,
    get_connection,
    init_db,
    add_decision,
    get_decision,
    get_open_decisions,
    update_decision_status,
    add_outcome,
    get_outcome,
    add_rule_version,
    get_rule_version,
    get_latest_rule_version,
)

__version__ = "0.2.0"
__all__ = [
    "get_conn",
    "get_connection",
    "init_db",
    "add_decision",
    "get_decision",
    "get_open_decisions",
    "update_decision_status",
    "add_outcome",
    "get_outcome",
    "add_rule_version",
    "get_rule_version",
    "get_latest_rule_version",
]