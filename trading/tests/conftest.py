"""Shared pytest fixtures — test isolation from production state.

Audit t_7c4728a7: running the suite rewrote production
~/.trading/execution/safety_state.json (mtime churn every run). Root cause:
``trading.config`` resolves ``EXECUTION_CONFIG["state_dir"]`` to the absolute
production path at import time and ``trading.execution.safety`` snapshots it
into ``_SAFETY_DEFAULTS`` — so patching ``os.path.expanduser`` (the TP-009
approach) cannot redirect it. Any SafetyEngine built through
``run_auto_trade()``/``_make_safety()`` (no explicit config) persisted to
production via ``_save_state()`` (e.g. ``update_drawdown()`` saves
unconditionally).

This autouse fixture redirects every SafetyEngine state path into the
per-test ``tmp_path`` instead, at both the config dict and the safety-module
snapshot. Tests that construct SafetyEngine with an explicit ``state_dir``
override are unaffected.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_safety_state(tmp_path, monkeypatch):
    """Point all SafetyEngine state paths at tmp_path for every test."""
    from trading import config as trading_config
    from trading.execution import safety as safety_mod

    exec_dir = tmp_path / "execution"
    targets = [trading_config.EXECUTION_CONFIG]
    if safety_mod._SAFETY_DEFAULTS is not None:
        targets.append(safety_mod._SAFETY_DEFAULTS)
    for target in targets:
        monkeypatch.setitem(target, "state_dir", str(exec_dir))
        monkeypatch.setitem(target, "macro_state_path", str(exec_dir / "macro_breaker.json"))
        monkeypatch.setitem(target, "emergency_stop_path", str(exec_dir / "EMERGENCY_STOP"))
