"""TP-001 regression coverage: order lifecycle classification (open vs terminal).

Deterministic and production-state free: every store under test is a temporary
directory, and no live order file is read or written.

The defect this pins down (found 2026-09-15): ``OPEN``/``TERMINAL`` lived inside
the ``OrderStatus`` class body. ``OrderStatus`` mixes in ``str``, so Enum turned
those frozensets into MEMBERS, ``status in OrderStatus.OPEN`` fell through to
``str.__contains__``, and ``"FILLED"`` — a substring of ``"PARTIALLY_FILLED"`` —
made every filled order report as open (18 filled orders listed as open).
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading.execution import models as models_mod  # noqa: E402
from trading.execution.models import (  # noqa: E402
    OPEN_STATUSES,
    TERMINAL_STATUSES,
    OrderStatus,
    is_open,
    is_terminal,
)
from trading.execution.order_store import OrderStore  # noqa: E402


OPEN_EXPECTED = {"PENDING", "NEW", "PARTIALLY_FILLED"}
TERMINAL_EXPECTED = {"FILLED", "REJECTED", "CANCELLED"}
ALL_EXPECTED = OPEN_EXPECTED | TERMINAL_EXPECTED


def test_order_status_contains_only_real_lifecycle_values():
    """The enum holds real broker states — no OPEN/TERMINAL pseudo-members."""
    assert {m.name for m in OrderStatus} == ALL_EXPECTED
    assert not hasattr(OrderStatus, "OPEN")
    assert not hasattr(OrderStatus, "TERMINAL")


def test_classification_collections_are_module_level_immutable_frozensets():
    assert isinstance(OPEN_STATUSES, frozenset)
    assert isinstance(TERMINAL_STATUSES, frozenset)
    assert not isinstance(OPEN_STATUSES, OrderStatus)
    assert not isinstance(TERMINAL_STATUSES, OrderStatus)

    assert OPEN_STATUSES == {
        OrderStatus.PENDING, OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED,
    }
    assert TERMINAL_STATUSES == {
        OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED,
    }
    # Open/terminal is a complete, disjoint partition of every real status.
    assert OPEN_STATUSES | TERMINAL_STATUSES == set(OrderStatus)
    assert OPEN_STATUSES & TERMINAL_STATUSES == frozenset()

    with pytest.raises(AttributeError):
        OPEN_STATUSES.add(OrderStatus.FILLED)  # frozenset: immutable


@pytest.mark.parametrize("status", list(OrderStatus), ids=lambda s: s.name)
@pytest.mark.parametrize("as_raw_string", [False, True], ids=["enum", "str"])
def test_every_status_is_classified_by_class_and_module_helpers(status, as_raw_string):
    value = status.value if as_raw_string else status
    expect_open = status.name in OPEN_EXPECTED
    expect_terminal = status.name in TERMINAL_EXPECTED

    assert is_open(value) is expect_open
    assert is_terminal(value) is expect_terminal
    assert OrderStatus.is_open(value) is expect_open
    assert OrderStatus.is_terminal(value) is expect_terminal
    # Mutually exclusive and exhaustive: never both, never neither.
    assert is_open(value) is not is_terminal(value)


def test_filled_not_reported_open_regression():
    """The exact 2026-09-15 defect: substring matching made FILLED look open."""
    assert "FILLED" in "PARTIALLY_FILLED"  # the substring trap itself
    assert is_open("FILLED") is False
    assert is_open(OrderStatus.FILLED) is False
    assert OrderStatus.is_open(OrderStatus.FILLED) is False
    assert models_mod.is_open("FILLED") is False
    assert is_terminal("FILLED") is True


def test_unknown_status_raises_instead_of_being_silently_unclassified():
    for helper in (is_open, is_terminal, OrderStatus.is_open, OrderStatus.is_terminal):
        with pytest.raises(ValueError):
            helper("WORKING")


@pytest.mark.parametrize("status", list(OrderStatus), ids=lambda s: s.name)
def test_legal_next_covers_every_status(status):
    nxt = OrderStatus.legal_next(status)
    assert isinstance(nxt, frozenset)
    eligible = set(OrderStatus) - {status}
    assert nxt <= eligible
    if status.name in TERMINAL_EXPECTED:
        assert nxt == frozenset(), f"{status.name} must be terminal (no transitions)"
    else:
        assert nxt, f"{status.name} must have at least one legal transition"


def _record(client_order_id: str, status: str) -> dict:
    return {
        "client_order_id": client_order_id,
        "order_id": f"order-{client_order_id}",
        "symbol": "KCB",
        "side": "BUY",
        "quantity": 10,
        "price": 50.0,
        "reason": "unit test",
        "status": status,
        "filled_quantity": 0,
        "filled_price": None,
        "average_fill_price": None,
        "created_at": "2026-09-15T00:00:00+00:00",
        "updated_at": "2026-09-15T00:00:00+00:00",
        "history": [{"ts": "2026-09-15T00:00:00+00:00", "status": status, "note": "fixture"}],
    }


def test_list_open_reaches_every_state_and_excludes_all_terminal_records(tmp_path):
    """Temporary store: list_open() == exactly the three open states."""
    store = OrderStore(str(tmp_path))

    # NEW — the state create() persists.
    store.create("cid-new", "order-new", "KCB", "BUY", 10, 50.0)
    # PARTIALLY_FILLED — legal fill transition.
    store.create("cid-partial", "order-partial", "KCB", "BUY", 10, 50.0)
    store.update_fills("cid-partial", 4, 50.0)
    # FILLED — full fill.
    store.create("cid-filled", "order-filled", "KCB", "BUY", 10, 50.0)
    store.update_fills("cid-filled", 10, 51.0)
    # REJECTED / CANCELLED — legal terminal transitions.
    store.create("cid-rejected", "order-rejected", "KCB", "BUY", 10, 50.0)
    store.transition("cid-rejected", OrderStatus.REJECTED, note="unit test")
    store.create("cid-cancelled", "order-cancelled", "KCB", "BUY", 10, 50.0)
    store.transition("cid-cancelled", OrderStatus.CANCELLED, note="unit test")
    # PENDING — create() starts at NEW and PENDING is not reachable through
    # transition(), so persist a legacy pending record directly.
    (tmp_path / "cid-pending.json").write_text(json.dumps(_record("cid-pending", "PENDING")))

    expected_open = {"cid-pending", "cid-new", "cid-partial"}
    expected_terminal = {"cid-filled", "cid-rejected", "cid-cancelled"}

    open_ids = {r["client_order_id"] for r in store.list_open()}
    all_ids = {r["client_order_id"] for r in store.all()}

    assert open_ids == expected_open
    assert all_ids == expected_open | expected_terminal
    assert open_ids.isdisjoint(expected_terminal)
    # Every returned record is genuinely non-terminal (guards against a
    # regression that reports terminal records without dropping them).
    assert all(not is_terminal(r["status"]) for r in store.list_open())


def test_list_open_returns_empty_when_every_record_is_terminal(tmp_path):
    """Temporary store: an all-terminal book has no open orders."""
    store = OrderStore(str(tmp_path))
    store.create("cid-filled-1", "order-1", "KCB", "BUY", 10, 50.0)
    store.update_fills("cid-filled-1", 10, 51.0)
    store.create("cid-filled-2", "order-2", "EQTY", "BUY", 4, 100.0)
    store.update_fills("cid-filled-2", 4, 101.0)
    store.create("cid-rejected-1", "order-3", "SCOM", "BUY", 5, 30.0)
    store.transition("cid-rejected-1", OrderStatus.REJECTED, note="unit test")
    store.create("cid-cancelled-1", "order-4", "SCOM", "BUY", 5, 30.0)
    store.transition("cid-cancelled-1", OrderStatus.CANCELLED, note="unit test")

    assert {r["status"] for r in store.all()} == {"FILLED", "REJECTED", "CANCELLED"}
    assert store.list_open() == []
