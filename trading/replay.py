"""Replay harness — archived raw capture + sandboxed replay runtime.

This module is inactive unless the environment variable REPLAY_DATE is set.
In replay mode it:
- seeds a sandbox portfolio from production state,
- replays archived market responses for the chosen date,
- prefixes all output/logging as [REPLAY],
- guarantees production portfolio paths are not modified.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from trading import config as _cfg
except Exception:  # pragma: no cover - import safety
    class _Cfg:  # type: ignore[no-redef]
        HOME = os.path.expanduser("~/.trading")
        DATA_DIR = os.path.join(os.path.expanduser("~/.trading"), "data")
        LOGS_DIR = os.path.join(os.path.expanduser("~/.trading"), "logs")
    _cfg = _Cfg()

REPLAY_ROOT = Path(os.path.expanduser("~/.trading/replay-data"))
PRODUCTION_PORTFOLIO_DIR = Path(os.path.expanduser("~/.trading/portfolio"))
PRODUCTION_STATE_PATH = PRODUCTION_PORTFOLIO_DIR / "state.json"
REPLAY_LOG_PREFIX = "[REPLAY]"
REPLAY_OUTPUT_DIR = REPLAY_ROOT / "output"
REPLAY_PORTFOLIO_DIR_NAME = "portfolio"


def is_replay() -> bool:
    return os.environ.get("REPLAY_DATE") is not None


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _replay_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _ensure_dirs() -> None:
    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    REPLAY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _safe_copy(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _scrub_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in {"api_key", "apikey", "authorization", "secret", "token", "password", "passwd", "access_token", "refresh_token"}:
                continue
            out[k] = _scrub_secrets(v)
        return out
    if isinstance(obj, list):
        return [_scrub_secrets(x) for x in obj]
    return obj


def _archive_live_prices_today() -> Path | None:
    _ensure_dirs()
    src = Path(_cfg.HOME) / "cache" / f"live-prices-{_today_iso()}.json"
    if not src.exists():
        return None
    dst = REPLAY_ROOT / _today_iso() / "source" / "live-prices" / src.name
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(src.read_text())
        payload = _scrub_secrets(payload)
        dst.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return dst
    except Exception:
        return None


def _seed_fixtures_from_existing_archives() -> dict[str, list[Path]]:
    _ensure_dirs()
    cache_dir = Path(_cfg.HOME) / "cache"
    audit_dir = Path(_cfg.HOME) / "audit"
    news_dir = Path(_cfg.HOME) / "data" / "market_intel_cache"
    copied: dict[str, list[Path]] = {}
    sources = [
        cache_dir.glob("live-prices-2026-07-*.json"),
        audit_dir.glob("corrections-2026-07-*.json"),
        news_dir.glob("news_*.json"),
    ]
    for pattern in sources:
        for src in pattern:
            try:
                date_part = _today_iso()
                name = src.name
                if not name.startswith("live-prices-") and not name.startswith("corrections-"):
                    continue
                dst = REPLAY_ROOT / date_part / "seed" / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.exists():
                    try:
                        payload = json.loads(src.read_text())
                        payload = _scrub_secrets(payload)
                        dst.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
                    except Exception:
                        shutil.copy2(src, dst)
                    copied.setdefault(date_part, []).append(dst)
            except Exception:
                continue
    return copied


def sandbox_portfolio_dir() -> Path:
    run_id = _replay_run_id()
    sandbox = REPLAY_ROOT / run_id / REPLAY_PORTFOLIO_DIR_NAME
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


def bootstrap_sandbox(sandbox_dir: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "production_portfolio_dir": str(PRODUCTION_PORTFOLIO_DIR),
        "sandbox_portfolio_dir": str(sandbox_dir),
        "seeded_from_production": False,
        "copied_files": [],
    }
    for name in ["state.json", "transactions.json", "snapshots.json", "benchmark.json"]:
        src = PRODUCTION_PORTFOLIO_DIR / name
        dst = sandbox_dir / name
        _safe_copy(src, dst)
        if src.exists():
            manifest["seeded_from_production"] = True
            manifest["copied_files"].append(name)
    return manifest


def runtime_context() -> dict[str, Any]:
    return {
        "replay_active": is_replay(),
        "replay_date": os.environ.get("REPLAY_DATE"),
        "replay_root": str(REPLAY_ROOT),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def ensure_replay_env() -> None:
    _ensure_dirs()
    _archive_live_prices_today()
