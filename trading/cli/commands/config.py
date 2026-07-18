"""``trading config`` — show / validate / edit configuration."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from .. import output
from ... import config


def show(quiet: bool = False, as_json: bool = False) -> int:
    """Print the active configuration.

    When ``as_json`` is True, emit the same JSON document on stdout
    (regardless of whether the user passes ``--quiet``). This matches
    the ``--json`` convention used by every other command.
    """
    snapshot: dict[str, Any] = {
        "PAIRS": list(config.PAIRS),
        "SMA_FAST": config.SMA_FAST,
        "SMA_SLOW": config.SMA_SLOW,
        "RSI_PERIOD": config.RSI_PERIOD,
        "RSI_OVERBOUGHT": config.RSI_OVERBOUGHT,
        "RSI_OVERSOLD": config.RSI_OVERSOLD,
        "LOOKBACK_DAYS": config.LOOKBACK_DAYS,
        "YFINANCE_TICKERS": dict(config.YFINANCE_TICKERS),
        "CONFIDENCE_MIN_RSI_DELTA": config.CONFIDENCE_MIN_RSI_DELTA,
        "CONFIRM_MAX_SPREAD_FRAC": config.CONFIRM_MAX_SPREAD_FRAC,
        "ASSET_FILTERS": dict(config.ASSET_FILTERS),
        "DUPLICATE_COOLDOWN_HOURS": config.DUPLICATE_COOLDOWN_HOURS,
        "SCORING_WEIGHTS": dict(config.SCORING_WEIGHTS),
        "RECOMMENDATION_THRESHOLDS": [
            [lo, label] for lo, label in config.RECOMMENDATION_THRESHOLDS
        ],
        "HOLDING_PERIODS": dict(config.HOLDING_PERIODS),
        "HOME": config.HOME,
        "DATA_DIR": config.DATA_DIR,
        "SIGNALS_CSV": config.SIGNALS_CSV,
        "BACKTEST_DIR": config.BACKTEST_DIR,
        "LOGS_DIR": config.LOGS_DIR,
    }
    if as_json or quiet:
        print(output.json_dumps(snapshot))
        return 0
    print(json.dumps(snapshot, indent=2, default=str))
    return 0


def validate_cmd(quiet: bool = False) -> int:
    """Validate the configuration. Returns 0 on success, 1 on warnings, 2 on failure."""
    errors: list[str] = []
    warnings: list[str] = []

    if not config.PAIRS:
        errors.append("PAIRS is empty")
    if config.SMA_FAST <= 0 or config.SMA_SLOW <= 0 or config.SMA_FAST >= config.SMA_SLOW:
        errors.append("SMA_FAST must be > 0 and < SMA_SLOW")
    if config.RSI_PERIOD <= 0:
        errors.append("RSI_PERIOD must be > 0")
    if config.RSI_OVERBOUGHT <= config.RSI_OVERSOLD:
        errors.append("RSI_OVERBOUGHT must be > RSI_OVERSOLD")
    if not config.YFINANCE_TICKERS:
        warnings.append("YFINANCE_TICKERS is empty — every pair will fall back to synthetic data")
    for pair in config.PAIRS:
        if pair not in config.YFINANCE_TICKERS:
            warnings.append(f"{pair} has no YFINANCE_TICKERS entry — will use synthetic data")

    result = {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }
    if quiet:
        print(output.json_dumps(result))
    else:
        if errors:
            print("FAIL")
            for e in errors:
                print(f"  ✗ {e}")
        else:
            print("OK")
        for w in warnings:
            print(f"  ! {w}")
    if errors:
        return 2
    if warnings:
        return 1
    return 0


def edit() -> int:
    """Open the config file in the user's editor.

    Defaults to ``$EDITOR``; falls back to ``nano`` or ``vi``.
    """
    import trading  # noqa: F401
    config_path = os.path.abspath(config.__file__)
    editor = os.environ.get("EDITOR", "nano")
    try:
        return subprocess.call([editor, config_path])
    except FileNotFoundError:
        print(f"Editor '{editor}' not found; set $EDITOR.", file=sys.stderr)
        return 1
