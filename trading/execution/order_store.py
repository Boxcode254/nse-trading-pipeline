"""Persistent order book with a validated state machine.

The order store is the single source of truth for order lifecycle inside the
execution engine. Every order the engine hands to a broker is persisted here
with a ``client_order_id`` (idempotency key) and a ``status`` drawn from
``OrderStatus``. State transitions are validated against :func:`OrderStatus.legal_next`
so an illegal transition (a broker returning nonsense, or a code bug) is caught
and alerted rather than silently corrupting the book.

Concurrency safety: order files are written atomically (temp file + os.replace)
so a crash mid-write cannot leave a half-written JSON that breaks the next run.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import OrderStatus, is_open


DEFAULT_STORE_DIR = os.path.expanduser("~/.trading/execution/orders")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderStoreError(Exception):
    """Base error for order store failures."""


class IllegalTransition(OrderStoreError):
    """Raised when a requested status change is not legal."""


class OrderStore:
    """JSON-file-backed order book keyed by ``client_order_id``."""

    def __init__(self, store_dir: str = DEFAULT_STORE_DIR):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    # ── File helpers (atomic) ──────────────────────────────────────────
    def _path(self, client_order_id: str) -> Path:
        # client_order_id is supplied by us (uuid4 / deterministic) — safe for
        # filesystem use. Guard against path traversal just in case.
        if not client_order_id or "/" in client_order_id or ".." in client_order_id:
            raise OrderStoreError(f"Invalid client_order_id: {client_order_id!r}")
        return self.store_dir / f"{client_order_id}.json"

    def _write_atomic(self, path: Path, record: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.store_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(record, f, indent=2)
            os.replace(tmp, path)  # atomic on same filesystem
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── CRUD ───────────────────────────────────────────────────────────
    def create(
        self,
        client_order_id: str,
        order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        price: Optional[float],
        reason: str = "",
    ) -> dict:
        """Persist a new order in PENDING then move it to NEW.

        Idempotent on ``client_order_id``: if the key already exists, the
        existing record is returned unchanged (so a retried submission cannot
        create a duplicate order).
        """
        path = self._path(client_order_id)
        if path.exists():
            existing = json.loads(path.read_text())
            if existing.get("client_order_id") == client_order_id:
                return existing  # dedup — already tracked

        record = {
            "client_order_id": client_order_id,
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,           # INTENDED quantity
            "price": price,
            "reason": reason,
            "status": OrderStatus.NEW.value,
            "filled_quantity": 0,
            "filled_price": None,
            "average_fill_price": None,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "history": [
                {"ts": _now_iso(), "status": OrderStatus.NEW.value, "note": "order created"}
            ],
        }
        self._write_atomic(path, record)
        return record

    def get(self, client_order_id: str) -> Optional[dict]:
        path = self._path(client_order_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def exists(self, client_order_id: str) -> bool:
        return self._path(client_order_id).exists()

    def transition(
        self,
        client_order_id: str,
        new_status: OrderStatus,
        *,
        filled_quantity: Optional[int] = None,
        filled_price: Optional[float] = None,
        note: str = "",
    ) -> dict:
        """Move an order to ``new_status``, validating the transition.

        Raises :class:`IllegalTransition` if the move is not allowed. On a fill
        (PARTIALLY_FILLED / FILLED) the filled fields are updated and an
        average-fill price is recomputed.
        """
        record = self.get(client_order_id)
        if record is None:
            raise OrderStoreError(f"Unknown order: {client_order_id}")

        current = OrderStatus(record["status"])
        if new_status == current:
            return record  # no-op

        if new_status not in OrderStatus.legal_next(current):
            raise IllegalTransition(
                f"Illegal transition {current.value} -> {new_status.value} "
                f"for {client_order_id}"
            )

        # Update filled fields for fill states.
        if new_status in (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED):
            if filled_quantity is None or filled_price is None:
                raise OrderStoreError(
                    "filled_quantity and filled_price required for fill transition"
                )
            prev_filled = record.get("filled_quantity", 0)
            prev_avg = record.get("average_fill_price")
            total_filled = prev_filled + filled_quantity
            if prev_avg is not None and prev_filled > 0:
                new_avg = (
                    (prev_avg * prev_filled) + (filled_price * filled_quantity)
                ) / total_filled
            else:
                new_avg = float(filled_price)
            record["filled_quantity"] = total_filled
            record["filled_price"] = filled_price
            record["average_fill_price"] = round(new_avg, 6)
            record["quantity"] = record.get("quantity", total_filled)  # intended stays

        record["status"] = new_status.value
        record["updated_at"] = _now_iso()
        record["history"].append(
            {"ts": _now_iso(), "status": new_status.value, "note": note}
        )
        self._write_atomic(self._path(client_order_id), record)
        return record

    def update_fills(
        self,
        client_order_id: str,
        filled_quantity: int,
        filled_price: float,
    ) -> dict:
        """Record a fill as an ABSOLUTE cumulative total.

        ``filled_quantity`` is the total shares filled so far (as a broker
        would report it), not a per-call delta. The delta used for the
        volume-weighted average is derived internally so repeated updates from
        a broker status poll accumulate correctly instead of double-counting.
        """
        record = self.get(client_order_id)
        if record is None:
            raise OrderStoreError(f"Unknown order: {client_order_id}")
        intended = record["quantity"]
        prev_filled = record.get("filled_quantity", 0)
        delta = filled_quantity - prev_filled
        if delta <= 0:
            # No new shares since last update — just return current record.
            if filled_quantity >= intended and OrderStatus(record["status"]) != OrderStatus.FILLED:
                return self.transition(
                    client_order_id, OrderStatus.FILLED,
                    filled_quantity=0, filled_price=filled_price,
                    note="marked fully filled (no new shares)",
                )
            return record
        new_state = (
            OrderStatus.FILLED if filled_quantity >= intended
            else OrderStatus.PARTIALLY_FILLED
        )
        return self.transition(
            client_order_id, new_state,
            filled_quantity=delta, filled_price=filled_price,
            note="fill update",
        )

    def list_open(self) -> list[dict]:
        """Return all orders that are not in a terminal state."""
        out = []
        for p in self.store_dir.glob("*.json"):
            if p.suffix == ".tmp":
                continue
            try:
                rec = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if is_open(rec["status"]):
                out.append(rec)
        return out

    def all(self) -> list[dict]:
        out = []
        for p in self.store_dir.glob("*.json"):
            if p.suffix == ".tmp":
                continue
            try:
                out.append(json.loads(p.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
        return out
