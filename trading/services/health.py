"""Health service — the ``trading doctor`` command.

Performs a complete health check of every subsystem and returns a
structured dict the CLI can format and exit on.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .. import config
from . import market


def _check(name: str, ok: bool, message: str = "") -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "message": message}


def _check_market_data() -> dict[str, Any]:
    """Check that at least one configured pair can be fetched."""
    try:
        t0 = time.time()
        frames = market.fetch_all()
        elapsed = time.time() - t0
        n_ok = len(frames)
        if n_ok == 0:
            return _check("market_data", False, "no pairs returned data")
        return _check(
            "market_data",
            True,
            f"{n_ok}/{len(config.PAIRS)} pairs OK ({elapsed:.1f}s)",
        )
    except Exception as exc:  # noqa: BLE001
        return _check("market_data", False, f"{type(exc).__name__}: {exc}")


def _check_yfinance() -> dict[str, Any]:
    """Check that yfinance is importable + the forex fetcher is reachable."""
    try:
        from ..fetchers import forex as forex_fetcher
        df = forex_fetcher.fetch_data("EUR/USD", days=10)
        if df is None or df.empty:
            return _check("yfinance", False, "yfinance returned no data")
        return _check("yfinance", True, f"{len(df)} bars for EUR/USD")
    except Exception as exc:  # noqa: BLE001
        return _check("yfinance", False, f"{type(exc).__name__}: {exc}")


def _check_tvdatafeed() -> dict[str, Any]:
    """Check that tvDatafeed is importable."""
    try:
        import importlib
        importlib.import_module("tvDatafeed")
        return _check("tvdatafeed", True, "tvDatafeed importable")
    except Exception as exc:  # noqa: BLE001
        return _check("tvdatafeed", False, f"tvDatafeed not available: {type(exc).__name__}")


def _check_db() -> dict[str, Any]:
    """Check that signals.csv and the logs directory are writable."""
    try:
        config.ensure_dirs()
        if not os.path.isdir(config.LOGS_DIR):
            return _check("storage", False, f"logs dir missing: {config.LOGS_DIR}")
        # signals.csv might not exist yet — that's OK
        if os.path.exists(config.SIGNALS_CSV):
            size = os.path.getsize(config.SIGNALS_CSV)
            return _check("storage", True, f"signals.csv present ({size} bytes)")
        return _check("storage", True, "signals.csv will be created on first run")
    except Exception as exc:  # noqa: BLE001
        return _check("storage", False, f"{type(exc).__name__}: {exc}")


def _check_logs() -> dict[str, Any]:
    """Check that the logs directory contains recent runs."""
    try:
        if not os.path.isdir(config.LOGS_DIR):
            return _check("logs", False, "logs directory missing")
        files = sorted(os.listdir(config.LOGS_DIR))
        if not files:
            return _check("logs", True, "no runs logged yet")
        latest = files[-1]
        return _check("logs", True, f"{len(files)} log file(s), latest: {latest}")
    except Exception as exc:  # noqa: BLE001
        return _check("logs", False, f"{type(exc).__name__}: {exc}")


def _check_config() -> dict[str, Any]:
    """Validate the configuration has the required keys."""
    try:
        required = ["PAIRS", "SMA_FAST", "SMA_SLOW", "RSI_PERIOD"]
        missing = [k for k in required if not hasattr(config, k)]
        if missing:
            return _check("config", False, f"missing keys: {missing}")
        if not config.PAIRS:
            return _check("config", False, "PAIRS is empty")
        return _check("config", True, f"{len(config.PAIRS)} pairs configured")
    except Exception as exc:  # noqa: BLE001
        return _check("config", False, f"{type(exc).__name__}: {exc}")


def _check_strategy_registry() -> dict[str, Any]:
    """Verify the strategy registry has the benchmark and at least one experimental."""
    try:
        from ..strategies import REGISTRY
        if "A" not in REGISTRY:
            return _check("strategies", False, "benchmark Strategy A missing")
        n = len(REGISTRY)
        return _check("strategies", True, f"{n} strategies registered")
    except Exception as exc:  # noqa: BLE001
        return _check("strategies", False, f"{type(exc).__name__}: {exc}")


def doctor() -> dict[str, Any]:
    """Run the full health check and return a structured result.

    Output schema::

        {
          "health_score": 0-100,
          "status": "healthy" | "warning" | "failure",
          "checks": [ {"name": str, "ok": bool, "message": str}, ... ],
          "warnings": [str, ...],
          "errors": [str, ...],
          "recommendations": [str, ...],
        }

    Status thresholds: 100=healthy (all green), 70-99=warning, <70=failure.
    """
    checks: list[dict[str, Any]] = []
    for fn in (
        _check_config,
        _check_market_data,
        _check_yfinance,
        _check_tvdatafeed,
        _check_db,
        _check_logs,
        _check_strategy_registry,
    ):
        try:
            checks.append(fn())
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": fn.__name__, "ok": False, "message": f"{type(exc).__name__}: {exc}"})

    n_total = len(checks)
    n_ok = sum(1 for c in checks if c["ok"])
    score = int(round(100.0 * n_ok / n_total)) if n_total else 0

    warnings = [c["name"] for c in checks if c["ok"] is False and "no " in (c.get("message", "").lower())]
    errors = [c["name"] for c in checks if c["ok"] is False and c["name"] not in warnings]

    if score >= 100:
        status = "healthy"
    elif score >= 70:
        status = "warning"
    else:
        status = "failure"

    recommendations: list[str] = []
    if not any(c["name"] == "yfinance" and c["ok"] for c in checks):
        recommendations.append("yfinance is unreachable — the engine will fall back to synthetic data.")
    if not any(c["name"] == "market_data" and c["ok"] for c in checks):
        recommendations.append("No market data could be fetched — check your network and config.")
    if errors:
        recommendations.append("Resolve errors above to restore full health.")

    return {
        "health_score": score,
        "status": status,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "recommendations": recommendations,
    }
