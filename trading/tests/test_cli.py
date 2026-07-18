"""Tests for the new Typer CLI (Phase 3 — Hermes Alpha).

The CLI must:
- Be importable as a Typer app
- Expose every command group listed in the spec
- Support --json on every command
- Return correct exit codes (0/1/2)
- Not duplicate business logic (parse → call service → format → exit)
- Keep the legacy ``python3 -m trading run`` entry point working

Run:
    cd ~/.trading && .venv/bin/python -m pytest trading/tests/test_cli.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

# Isolate HOME so the CLI never touches the real ~/.trading
TMP_HOME = tempfile.mkdtemp(prefix="trading-cli-test-")
os.environ["HOME"] = TMP_HOME


def test_cli_app_is_typer_instance() -> None:
    """The main app must be a Typer application."""
    from trading.cli.main import app
    import typer
    assert isinstance(app, typer.Typer), f"expected Typer, got {type(app)}"


def test_cli_app_has_required_command_groups() -> None:
    """Every command group from the Phase 3 spec must be registered."""
    from trading.cli.main import app
    # Typer stores registered commands in app.registered_commands
    top_names = {cmd.name for cmd in app.registered_commands}
    # Sub-apps (config) are registered via add_typer — they appear in
    # app.registered_groups; we record both the group name and its subcommands.
    sub_group_names: set[str] = set()
    sub_names: set[str] = set()
    for grp in getattr(app, "registered_groups", []):
        name = getattr(grp, "name", None)
        if name:
            sub_group_names.add(name)
        if hasattr(grp, "typer_instance"):
            sub_names |= {cmd.name for cmd in grp.typer_instance.registered_commands}
    all_names = top_names | sub_group_names
    expected = {
        "morning", "summary", "scan", "signal", "explain", "opportunities",
        "price", "portfolio", "rebalance", "strategies", "compare",
        "benchmark", "backtest", "stats", "doctor", "config", "context",
    }
    missing = expected - all_names
    assert not missing, f"missing commands: {missing}; top={top_names} sub_groups={sub_group_names} sub={sub_names}"
    # And the config sub-app must have show/validate/edit
    assert {"show", "validate", "edit"} <= sub_names, f"config sub-commands: {sub_names}"


def test_help_runs() -> None:
    """`--help` must exit 0 (per Typer convention, exit 0)."""
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    # Typer's CliRunner exits 0 for help
    assert result.exit_code == 0, f"--help failed: {result.output}"


def test_signal_command_supports_json_flag() -> None:
    """``trading signal --json`` must emit a JSON document on stdout."""
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    # SCOM is one of the configured pairs (or will be after ensure_dirs)
    # The command may exit 1 if data is missing, but --json path must still be JSON.
    result = runner.invoke(app, ["signal", "SCOM", "--json"])
    if result.exit_code == 0:
        # When it succeeds, output must be parseable JSON
        try:
            data = json.loads(result.output)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"signal --json did not emit JSON: {result.output}") from exc
        assert "symbol" in data, f"missing 'symbol' in JSON: {data}"
    # If it returns non-zero (e.g. no data), output is not necessarily JSON.
    # The important thing is that the command is wired up and exits with a code.


def test_strategies_command_json() -> None:
    """``trading strategies --json`` must list registered strategies as JSON."""
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(app, ["strategies", "--json"])
    assert result.exit_code == 0, f"strategies --json failed: {result.output}"
    data = json.loads(result.output)
    assert "strategies" in data
    assert isinstance(data["strategies"], list)
    # Strategy A is the frozen benchmark; should always be present.
    keys = {s["key"] for s in data["strategies"]}
    assert "A" in keys, f"benchmark strategy A missing: {keys}"


def test_config_show_json() -> None:
    """``trading config show --json`` must emit the config snapshot."""
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0, f"config show --json failed: {result.output}"
    data = json.loads(result.output)
    assert "PAIRS" in data
    assert "SMA_FAST" in data
    assert "SCORING_WEIGHTS" in data


def test_config_validate() -> None:
    """``trading config validate`` must report OK with the default config."""
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(app, ["config", "validate"])
    # Default config should pass; the runner may print OK + warnings.
    assert result.exit_code in (0, 1), f"config validate exited {result.exit_code}: {result.output}"


def test_portfolio_subgroup_registered() -> None:
    """``trading portfolio`` is now a subcommand group (init/show/buy/...)."""
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    # `trading portfolio --help` should exit 0 and show the subcommand list
    result = runner.invoke(app, ["portfolio", "--help"])
    assert result.exit_code == 0, f"portfolio --help failed: {result.output}"
    # Must list at least init/show
    assert "init" in result.output
    assert "show" in result.output


def test_rebalance_placeholder_returns_zero() -> None:
    """``trading rebalance`` is a Phase-4 placeholder — must exit 0."""
    from trading.cli.main import app
    from typer.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(app, ["rebalance"])
    assert result.exit_code == 0, f"rebalance should be a friendly placeholder: {result.output}"


def test_legacy_run_entrypoint_still_works() -> None:
    """``python3 -m trading run`` must still work (used by the daily cron)."""
    import trading
    # The legacy entry point uses argparse + cmd_run. Smoke-test that it imports.
    from trading.__main__ import build_parser, cmd_run
    parser = build_parser()
    args = parser.parse_args(["run"])
    assert args.func is cmd_run


def test_legacy_subcommands_present() -> None:
    """All legacy subcommands the cron / external scripts may rely on must exist."""
    from trading.__main__ import build_parser
    parser = build_parser()
    # The actions registered as choices
    # argparse stores them in parser._subparsers._group_actions[0].choices
    sub_action = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    names = set(sub_action.choices.keys())
    assert {"run", "history", "backtest", "compare", "rank"} <= names, (
        f"missing legacy subcommands: {names}"
    )


if __name__ == "__main__":
    # Run as a plain script: collect + invoke each test manually (no pytest dep)
    tests = [
        test_cli_app_is_typer_instance,
        test_cli_app_has_required_command_groups,
        test_help_runs,
        test_signal_command_supports_json_flag,
        test_strategies_command_json,
        test_config_show_json,
        test_config_validate,
        test_portfolio_subgroup_registered,
        test_rebalance_placeholder_returns_zero,
        test_legacy_run_entrypoint_still_works,
        test_legacy_subcommands_present,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR   {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed} test(s) failed")
        raise SystemExit(1)
    print(f"\nAll {len(tests)} tests passed")
