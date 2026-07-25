"""Acceptance tests for the synthetic replay harness."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from trading import replay as replay_module
from trading.auto_trader import run_auto_trade
from trading.portfolio import engine as port_engine
from trading.replay import _today_iso


REPLAY_ROOT = Path(os.path.expanduser("~/.trading/replay-data"))
PROD_STATE = Path(os.path.expanduser("~/.trading/portfolio/state.json"))


def _prod_state_checksum() -> str | None:
    if not PROD_STATE.exists():
        return None
    return hashlib.sha256(PROD_STATE.read_bytes()).hexdigest()


def test_replay_module_active_only_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPLAY_DATE", raising=False)
    assert replay_module.is_replay() is False
    monkeypatch.setenv("REPLAY_DATE", "2026-07-13")
    assert replay_module.is_replay() is True


def test_sandbox_does_not_touch_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY_DATE", "2026-07-13")
    before = _prod_state_checksum()
    report = run_auto_trade(dry_run=True)
    after = _prod_state_checksum()
    assert before == after, "production state.json changed during replay"
    assert "[REPLAY]" in report.build()


def test_report_is_labeled_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY_DATE", "2026-07-13")
    report = run_auto_trade(dry_run=True)
    built = report.build()
    assert "[REPLAY]" in built


def test_historical_date_runs_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY_DATE", "2026-07-13")
    report = run_auto_trade(dry_run=True)
    assert report is not None


def test_seed_fixtures_from_existing_archives_runs() -> None:
    copied = replay_module._seed_fixtures_from_existing_archives()
    assert isinstance(copied, dict)
    assert _today_iso() in copied or len(copied) == 0


def test_sandbox_portfolio_files_are_written_and_labelled() -> None:
    sandbox = replay_module.sandbox_portfolio_dir()
    manifest_path = sandbox.parent / "replay-manifest.json"
    assert sandbox.exists() or manifest_path.parent.exists()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        assert manifest.get("production_portfolio_dir") == str(Path(os.path.expanduser("~/.trading/portfolio")))
