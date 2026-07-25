"""Execution Engine — orchestrates safety checks + broker execution.

Phase 0 hardening (2026-07-25). This module now owns the full order lifecycle:

1. **State machine** — every order moves through ``OrderStatus``
   (PENDING → NEW → PARTIALLY_FILLED → FILLED | REJECTED | CANCELLED).
   Transitions are validated by ``OrderStore``; illegal transitions are
   impossible by construction.
2. **Idempotency** — orders carry a ``client_order_id``. A retried submission
   (cron overlap, network retry, double-click) is detected *before* it reaches
   the broker and short-circuits to the already-recorded result. This is the
   primary defence against double-fills.
3. **Resilience** — broker calls are guarded by a persisted circuit breaker and
   a bounded timeout with exponential backoff. A hung broker fails fast instead
   of blocking the auto-trader; repeated failures trip the breaker.
4. **Reconciliation** — after a fill, intended quantity/price are compared
   against what actually filled. A mismatch (partial fill, wrong price) raises
   an alert so a silent short-fill can never go unnoticed.

All persistence goes through the execution dir so a crash mid-run leaves a
recoverable, reconcilable order book rather than lost state.
"""
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from .models import (
    OrderRequest,
    OrderResult,
    ExecutionReport,
    AccountInfo,
    OrderStatus,
    is_open,
    is_terminal,
)
from .broker import BrokerBase
from .safety import SafetyEngine
from .order_store import OrderStore
from .circuit_breaker import CircuitBreaker
from .retry import call_with_timeout, execute_resilient
from .alerting import alert


# Price tolerance for reconciliation (paper fills are exact; real brokers may
# slip by a tick). 0.5% is generous enough to ignore tick noise but tight
# enough to catch a genuinely wrong fill.
_RECON_PRICE_TOL_PCT = 0.5


class ExecutionEngine:
    """Orchestrates the full, hardened execution pipeline."""

    def __init__(
        self,
        broker: BrokerBase,
        safety: Optional[SafetyEngine] = None,
        *,
        order_store: Optional[OrderStore] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        broker_timeout: float = 10.0,
        max_retries: int = 2,
        alerts_path: Optional[str] = None,
    ):
        self.broker = broker
        self.safety = safety or SafetyEngine()
        self.order_store = order_store or OrderStore()
        self.breaker = circuit_breaker or CircuitBreaker()
        self.broker_timeout = broker_timeout
        self.max_retries = max_retries
        self._alerts_path = alerts_path
        self._connected = False

    # ── Connection ────────────────────────────────────────────────────
    def connect(self) -> bool:
        self._connected = self.broker.connect()
        return self._connected

    def disconnect(self) -> bool:
        ok = self.broker.disconnect()
        self._connected = False
        return ok

    # ── Idempotency key ───────────────────────────────────────────────
    def _resolve_client_id(self, request: OrderRequest) -> str:
        """Return the caller's idempotency key, generating one if absent."""
        if request.client_order_id:
            return request.client_order_id
        import uuid
        cid = f"cli-{uuid.uuid4().hex}"
        request.client_order_id = cid
        return cid

    # ── Core execute ──────────────────────────────────────────────────
    def execute(
        self,
        request: OrderRequest,
        portfolio_state: Optional[Dict[str, Any]] = None,
        account: Optional[AccountInfo] = None,
    ) -> ExecutionReport:
        """Execute an order through the full safety-checked, reconciled pipeline."""
        ts = datetime.now(timezone.utc).isoformat()

        # Auto-connect if not connected
        if not self._connected:
            self.connect()

        # ── IDEMPOTENCY ──
        client_id = self._resolve_client_id(request)
        existing = self.order_store.get(client_id)
        if existing is not None:
            estatus = OrderStatus(existing["status"])
            if is_terminal(estatus):
                # Already settled — return the recorded outcome, do NOT re-send.
                return self._report_from_store(existing, ts)
            # Open order: reconcile current broker state and return it.
            self._reconcile_open(client_id)
            live = self.order_store.get(client_id)
            if live is not None and is_terminal(OrderStatus(live["status"])):
                return self._report_from_store(live, ts)
            return ExecutionReport(
                success=False,
                message=(
                    f"Order {client_id} already in flight "
                    f"({live['status'] if live else 'unknown'}) — duplicate submission blocked"
                ),
                timestamp=ts,
            )

        # ── Portfolio context for safety ──
        if portfolio_state is None:
            portfolio_state = self._get_portfolio_state()
        if account is None:
            account = self.broker.get_account()

        # ── Safety gate — NEVER bypassed ──
        verdict = self.safety.check_order(request, portfolio_state, account)
        if not verdict.allowed:
            # Record the blocked intent so it is auditable, then bail.
            self.order_store.create(
                client_id, order_id="", symbol=request.symbol,
                side=request.side, quantity=request.quantity,
                price=request.price, reason=f"BLOCKED: {verdict.reason}",
            )
            try:
                self.order_store.transition(client_id, OrderStatus.REJECTED,
                                            note=f"safety: {verdict.reason}")
            except Exception:
                pass
            return ExecutionReport(
                success=False, safety=verdict, message=verdict.reason, timestamp=ts,
            )

        # ── Circuit breaker: fail fast if broker is known-down ──
        if not self.breaker.allow():
            alert(
                f"Broker {self.broker.name} breaker OPEN — refusing order {client_id}",
                severity="CRITICAL",
                context={"symbol": request.symbol, "side": request.side,
                         "breaker": self.breaker.snapshot()},
                alerts_path=self._alerts_path,
            )
            return ExecutionReport(
                success=False, safety=verdict,
                message="Circuit breaker OPEN — broker unavailable, order refused",
                timestamp=ts,
            )

        # ── Track the order in NEW before we touch the broker ──
        self.order_store.create(
            client_id, order_id="", symbol=request.symbol,
            side=request.side, quantity=request.quantity,
            price=request.price, reason=request.reason,
        )

        # ── Broker call with timeout + backoff (breaker-aware) ──
        try:
            completed, result, err = execute_resilient(
                lambda: self.broker.place_order(request),
                timeout=self.broker_timeout,
                max_retries=self.max_retries,
            )
        except Exception as exc:  # execute_resilient exhausted retries on error
            self.breaker.on_failure()
            alert(f"Broker {self.broker.name} place_order failed: {exc}",
                  severity="CRITICAL", context={"client_id": client_id},
                  alerts_path=self._alerts_path)
            self._mark_rejected(client_id, f"broker error: {exc}")
            return ExecutionReport(success=False, safety=verdict,
                                   message=f"Broker error: {exc}", timestamp=ts)

        if not completed:
            # TIMEOUT / unknown outcome — do NOT assume failure. Reconcile.
            self.breaker.on_failure()
            alert(
                f"Broker {self.broker.name} place_order TIMEOUT for {client_id} "
                f"(outcome unknown) — reconciling",
                severity="CRITICAL",
                context={"symbol": request.symbol, "side": request.side},
                alerts_path=self._alerts_path,
            )
            reconciled = self._reconcile_open(client_id)
            if reconciled is not None and is_terminal(OrderStatus(reconciled["status"])):
                return self._report_from_store(reconciled, ts)
            return ExecutionReport(
                success=False, safety=verdict,
                message="Order outcome UNKNOWN after timeout — reconciling, do not retry blindly",
                timestamp=ts,
            )

        # Completed normally (result is an OrderResult or None)
        self.breaker.on_success()
        if result is None or not getattr(result, "success", False):
            self._mark_rejected(client_id, "broker returned failure")
            msg = getattr(result, "message", "broker returned failure") if result else "no result"
            return ExecutionReport(success=False, safety=verdict, message=msg, timestamp=ts)

        # ── Record the fill in the store + RECONCILE ──
        fill_qty = getattr(result, "filled_quantity", 0) or result.quantity
        fill_price = getattr(result, "filled_price", None) or result.price
        try:
            rec = self.order_store.update_fills(client_id, fill_qty, fill_price)
        except Exception:
            rec = self.order_store.get(client_id) or {}

        self._reconcile(result, client_id, request, rec)

        # Safety bookkeeping (daily counters) on success
        self.safety.record_trade(result)

        return ExecutionReport(
            success=True,
            order=result,
            safety=verdict,
            message=result.message,
            timestamp=ts,
        )

    # ── Order status / cancel (real implementations) ──
    def get_order_status(self, client_order_id: str) -> Optional[OrderResult]:
        """Return the current status of an order by client_order_id.

        Reads the persisted order book; if the order is still open, queries the
        broker and reconciles the result back into the store.
        """
        rec = self.order_store.get(client_order_id)
        if rec is None:
            return None
        status = OrderStatus(rec["status"])
        if is_open(status):
            self._reconcile_open(client_order_id)
            rec = self.order_store.get(client_order_id) or rec
        return _order_result_from_record(rec)

    def cancel_order(self, client_order_id: str) -> bool:
        """Attempt to cancel an open order. Returns True if cancelled."""
        rec = self.order_store.get(client_order_id)
        if rec is None:
            return False
        status = OrderStatus(rec["status"])
        if not is_open(status):
            return False  # already terminal — nothing to cancel
        try:
            ok = self.broker.cancel_order(rec.get("order_id", ""))
        except Exception as exc:
            alert(f"cancel_order broker error for {client_order_id}: {exc}",
                  severity="WARN", alerts_path=self._alerts_path)
            return False
        if ok:
            try:
                self.order_store.transition(client_order_id, OrderStatus.CANCELLED,
                                            note="cancelled via engine")
            except Exception:
                pass
            return True
        return False

    # ── Reconciliation ────────────────────────────────────────────────
    def _reconcile(
        self,
        result: OrderResult,
        client_id: str,
        request: OrderRequest,
        rec: dict,
    ) -> None:
        """Compare intended vs filled; alert on mismatch."""
        intended_qty = request.quantity
        intended_price = request.price
        filled_qty = getattr(result, "filled_quantity", 0) or result.quantity
        filled_price = getattr(result, "filled_price", None) or result.price

        mismatches = []
        if filled_qty < intended_qty:
            mismatches.append(
                f"PARTIAL FILL: wanted {intended_qty}, got {filled_qty} "
                f"({intended_qty - filled_qty} unfilled)"
            )
        if intended_price is not None and filled_price is not None and intended_price > 0:
            slip_pct = abs(filled_price - intended_price) / intended_price * 100
            if slip_pct > _RECON_PRICE_TOL_PCT:
                mismatches.append(
                    f"PRICE SLIP: intended {intended_price:.4f}, filled "
                    f"{filled_price:.4f} ({slip_pct:+.2f}%)"
                )

        if mismatches:
            alert(
                f"Reconciliation mismatch for {client_id} ({request.symbol} {request.side}): "
                + "; ".join(mismatches),
                severity="CRITICAL",
                context={"client_id": client_id, "intended_qty": intended_qty,
                         "filled_qty": filled_qty, "intended_price": intended_price,
                         "filled_price": filled_price},
                alerts_path=self._alerts_path,
            )

    def _reconcile_open(self, client_id: str) -> Optional[dict]:
        """Poll the broker for an open order and fold the result into the store."""
        rec = self.order_store.get(client_id)
        if rec is None:
            return None
        try:
            status_result = self.broker.get_order_status(rec.get("order_id", ""))
        except NotImplementedError:
            # Broker cannot report — leave open for manual review, alert once.
            alert(f"Broker {self.broker.name} has no get_order_status; "
                  f"order {client_id} left OPEN for manual review",
                  severity="WARN", alerts_path=self._alerts_path)
            return rec
        except Exception as exc:
            alert(f"get_order_status failed for {client_id}: {exc}",
                  severity="WARN", alerts_path=self._alerts_path)
            return rec

        if status_result is None:
            return rec
        # status_result is an OrderResult from the broker.
        fq = getattr(status_result, "filled_quantity", 0) or status_result.quantity
        fp = getattr(status_result, "filled_price", None) or status_result.price
        try:
            if fq >= rec["quantity"]:
                return self.order_store.update_fills(client_id, fq, fp)
            if fq > 0:
                return self.order_store.update_fills(client_id, fq, fp)
        except Exception:
            pass
        return rec

    def _mark_rejected(self, client_id: str, reason: str) -> None:
        try:
            self.order_store.transition(client_id, OrderStatus.REJECTED, note=reason)
        except Exception:
            pass

    def _report_from_store(self, rec: dict, ts: str) -> ExecutionReport:
        """Build an ExecutionReport from an already-settled store record."""
        result = _order_result_from_record(rec)
        success = rec["status"] in (OrderStatus.FILLED.value,)
        return ExecutionReport(
            success=success,
            order=result,
            message=f"resolved from order store ({rec['status']})",
            timestamp=ts,
        )

    # ── Status / introspection ────────────────────────────────────────
    def get_status(self) -> Dict[str, Any]:
        account = self.broker.get_account() if self._connected else None
        return {
            "connected": self._connected,
            "broker": self.broker.name,
            "account": asdict(account) if account else None,
            "safety": self.safety.get_status(),
            "circuit_breaker": self.breaker.snapshot(),
            "open_orders": [o["client_order_id"] for o in self.order_store.list_open()],
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


def _order_result_from_record(rec: dict) -> OrderResult:
    """Rehydrate an OrderResult from a persisted store record."""
    return OrderResult(
        success=rec["status"] == OrderStatus.FILLED.value,
        order_id=rec.get("order_id", ""),
        symbol=rec["symbol"],
        side=rec["side"],
        quantity=rec["quantity"],
        price=rec.get("price") or 0.0,
        total=(rec.get("price") or 0.0) * rec["quantity"],
        fee=0.0,
        status=rec["status"],
        message="",
        timestamp=rec.get("updated_at", ""),
        external_id="",
        filled_quantity=rec.get("filled_quantity", 0),
        filled_price=rec.get("filled_price"),
        average_fill_price=rec.get("average_fill_price"),
        client_order_id=rec.get("client_order_id"),
    )
