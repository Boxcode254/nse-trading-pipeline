"""Alerting for execution-layer incidents.

Two channels:
1. ``alerts.log`` — an append-only JSON-lines file under ``~/.trading/execution``.
   This is the durable record and is written synchronously; it must never raise.
2. Telegram — best-effort push to the operator. Failures are swallowed (logged
   to the alerts file) because an alerting failure must NEVER block a trade or
   crash the auto-trader.

Severity levels: INFO, WARN, CRITICAL. Reconciliation mismatches and breaker
trips are CRITICAL because they indicate money/order-state risk.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_ALERTS_PATH = os.path.expanduser("~/.trading/execution/alerts.log")

# Telegram delivery is optional. Env vars are read lazily so importing this
# module has no side effects and works in CI/tests without network.
_TG_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
_TG_CHAT_ENV = "TELEGRAM_CHAT_ID"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_alert(
    message: str,
    *,
    severity: str = "WARN",
    context: Optional[dict] = None,
    alerts_path: str = DEFAULT_ALERTS_PATH,
) -> dict:
    """Append a structured alert to alerts.log. Never raises."""
    record = {
        "ts": _now_iso(),
        "severity": severity,
        "message": message,
        "context": context or {},
    }
    try:
        path = Path(alerts_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # Last-resort: nothing we can do if the disk is full/unwritable.
        pass
    return record


def send_telegram(
    text: str,
    *,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    timeout: float = 8.0,
) -> bool:
    """Best-effort Telegram send. Returns True on success, False otherwise.

    Never raises — alerting must not block or crash the caller.
    """
    token = token or os.environ.get(_TG_TOKEN_ENV)
    chat_id = chat_id or os.environ.get(_TG_CHAT_ENV)
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 — best effort
        return False


def alert(
    message: str,
    *,
    severity: str = "WARN",
    context: Optional[dict] = None,
    telegram: bool = True,
    alerts_path: str = DEFAULT_ALERTS_PATH,
) -> dict:
    """Log an alert (always) and optionally push to Telegram (best effort)."""
    record = log_alert(message, severity=severity, context=context, alerts_path=alerts_path)
    if telegram and severity in ("WARN", "CRITICAL"):
        # Keep Telegram text short and actionable.
        prefix = "🔴" if severity == "CRITICAL" else "⚠️"
        send_telegram(f"{prefix} [{severity}] {message}", alerts_path=alerts_path)
    return record
