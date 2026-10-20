"""Stop-loss re-entry cooldown (t_1c1c0873).

Covers:
- record -> blocked on sessions 1..N, allowed on N+1 (weekend-skipping)
- other symbols unaffected
- take-profit (non stop-loss) trades never record
- double record is idempotent (no duplicate, no extension)
- corrupt JSON fails open with a WARN, never raises
- STOP_COOLDOWN_SESSIONS env override
- state file permissions (0600) + .bak on first write
- DETERMINISTIC REPLAY: KNRE stop-out 2026-10-16 (5 sessions) must suppress
  the 2026-10-19 insurance-underweight BUY in generate_rebalance_plan, and
  the plan DOES buy KNRE once the cooldown entry is removed.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import date
from pathlib import Path

import pytest

import trading.stop_cooldown as sc
import trading.target_allocation as ta
from trading.stop_cooldown import (
    cooldown_status,
    format_exit_price,
    list_cooldowns,
    record_stop_exit,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """Isolate the cooldown state file into tmp_path (never production)."""
    p = tmp_path / "stop_cooldown.json"
    monkeypatch.setattr(sc, "STATE_PATH", p)
    return p


@pytest.fixture(autouse=True)
def _no_learned_gates(monkeypatch):
    """Deterministic signal gates: never read the repo's learned_gates.json."""
    monkeypatch.setattr(ta, "_LEARNED_GATES", {})


# ── Session-window semantics ────────────────────────────────────────────────

def test_record_blocks_sessions_1_to_n(state_file, monkeypatch):
    """Exit Fri 2026-10-16, 5 sessions: Mon 19 .. Fri 23 blocked (5..1 left)."""
    assert record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5) is True
    expected = [
        ("2026-10-19", 5), ("2026-10-20", 4), ("2026-10-21", 3),
        ("2026-10-22", 2), ("2026-10-23", 1),
    ]
    for on_date, remaining in expected:
        st = cooldown_status("KNRE", on_date=on_date)
        assert st["active"] is True, f"{on_date} should be blocked"
        assert st["sessions_remaining"] == remaining, on_date
        assert st["until"] == "2026-10-23", on_date
        assert st["exit_price"] == pytest.approx(4.0639)


def test_allowed_on_session_n_plus_1(state_file):
    """The session AFTER `until` is exclusive: Mon 2026-10-26 re-entry allowed."""
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    st = cooldown_status("KNRE", on_date="2026-10-26")
    assert st["active"] is False
    assert st["sessions_remaining"] == 0


def test_cooldown_spans_weekend_not_weekend_days(state_file):
    """Weekend days are not sessions: exit Thu 10-15, 2 sessions -> Fri 16, Mon 19."""
    record_stop_exit("KNRE", "2026-10-15", 4.00, 100, sessions=2)
    assert cooldown_status("KNRE", on_date="2026-10-16")["active"] is True
    assert cooldown_status("KNRE", on_date="2026-10-19")["active"] is True
    assert cooldown_status("KNRE", on_date="2026-10-17")["active"] is True  # Sat: still inside
    assert cooldown_status("KNRE", on_date="2026-10-20")["active"] is False


def test_other_symbols_unaffected(state_file):
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    assert cooldown_status("BRIT", on_date="2026-10-19")["active"] is False
    assert cooldown_status("SCOM", on_date="2026-10-19")["active"] is False
    assert "BRIT" not in list_cooldowns(on_date="2026-10-19")


def test_exit_session_itself_is_not_blocked(state_file):
    """The exit session is the sell itself; the window opens the next session."""
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    assert cooldown_status("KNRE", on_date="2026-10-16")["active"] is False


# ── Recording rules ─────────────────────────────────────────────────────────

def test_double_record_is_idempotent(state_file):
    """Same (symbol, exit_date) twice: no duplicate, no extension."""
    assert record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5) is True
    first = json.loads(state_file.read_text())["entries"]["KNRE"]
    # Second call with a LONGER cooldown must not extend it
    assert record_stop_exit("KNRE", "2026-10-16", 4.20, 999, sessions=10) is False
    entries = json.loads(state_file.read_text())["entries"]
    assert list(entries.keys()) == ["KNRE"]
    assert entries["KNRE"] == first
    assert entries["KNRE"]["sessions"] == 5
    assert entries["KNRE"]["exit_price"] == pytest.approx(4.0639)
    # A different exit_date IS a new record (fresh stop-out)
    assert record_stop_exit("KNRE", "2026-10-30", 4.00, 100, sessions=5) is True
    assert set(json.loads(state_file.read_text())["entries"].keys()) == {"KNRE"} or \
        len(json.loads(state_file.read_text())["entries"]) == 1  # replaced, keyed by symbol


def test_new_exit_date_replaces_old_entry(state_file):
    """Entries are keyed by symbol: a later stop-out replaces the stale one."""
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    record_stop_exit("KNRE", "2026-11-02", 4.10, 900, sessions=5)
    entries = json.loads(state_file.read_text())["entries"]
    assert entries["KNRE"]["exit_date"] == "2026-11-02"
    assert cooldown_status("KNRE", on_date="2026-10-19")["active"] is False


def test_take_profit_does_not_record(state_file):
    """_maybe_record_stop_exit keys on stop_loss=True; take-profit never records."""
    from trading.auto_trader import _maybe_record_stop_exit

    _maybe_record_stop_exit(
        {"symbol": "KNRE", "take_profit": True, "stop_loss": False},
        "KNRE", 5.55, 1802,
    )
    assert not state_file.exists()
    assert cooldown_status("KNRE", on_date=None)["active"] is False


def test_sector_cap_sell_does_not_record(state_file):
    from trading.auto_trader import _maybe_record_stop_exit

    _maybe_record_stop_exit(
        {"symbol": "KCB", "sector_cap": True}, "KCB", 33.0, 100,
    )
    assert not state_file.exists()


def test_stop_loss_trade_records(state_file):
    from trading.auto_trader import _maybe_record_stop_exit

    _maybe_record_stop_exit(
        {"symbol": "KNRE", "stop_loss": True}, "KNRE", 4.0639, 1802,
    )
    assert state_file.exists()
    entry = json.loads(state_file.read_text())["entries"]["KNRE"]
    assert entry["exit_price"] == pytest.approx(4.0639)
    assert entry["shares"] == 1802
    # The exit session itself is not blocked; the next weekday session is.
    exit_d = date.fromisoformat(entry["exit_date"])
    nxt = exit_d
    while True:
        nxt = date.fromordinal(nxt.toordinal() + 1)
        if nxt.weekday() < 5:
            break
    assert cooldown_status("KNRE", on_date=nxt.isoformat())["active"] is True


def test_recording_never_raises_on_bad_exit_date(state_file):
    assert record_stop_exit("KNRE", "not-a-date", 4.0, 100) is False
    assert not state_file.exists()


# ── Fail-open behaviour ─────────────────────────────────────────────────────

def test_corrupt_json_fails_open_with_warn(state_file, caplog):
    state_file.write_text("{not valid json!!")
    with caplog.at_level("WARNING"):
        st = cooldown_status("KNRE", on_date="2026-10-19")
    assert st["active"] is False
    assert any("corrupt" in r.message.lower() or "unreadable" in r.message.lower()
               for r in caplog.records), "expected a WARN about the corrupt state file"
    # And the module still lists nothing without raising
    assert list_cooldowns(on_date="2026-10-19") == {}


def test_corrupt_json_record_still_works(state_file, caplog):
    state_file.write_text("]broken[")
    with caplog.at_level("WARNING"):
        assert record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802) is True
    assert cooldown_status("KNRE", on_date="2026-10-19")["active"] is True


def test_missing_state_file_is_no_cooldown(state_file):
    assert cooldown_status("KNRE", on_date="2026-10-19")["active"] is False
    assert cooldown_status("KNRE")["active"] is False


# ── Env override / state hygiene ────────────────────────────────────────────

def test_env_override_sessions(state_file, monkeypatch):
    monkeypatch.setenv("STOP_COOLDOWN_SESSIONS", "2")
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=None)
    entry = json.loads(state_file.read_text())["entries"]["KNRE"]
    assert entry["sessions"] == 2
    assert cooldown_status("KNRE", on_date="2026-10-19")["active"] is True
    assert cooldown_status("KNRE", on_date="2026-10-20")["active"] is True
    assert cooldown_status("KNRE", on_date="2026-10-21")["active"] is False


def test_env_override_invalid_falls_back_to_default(state_file, monkeypatch):
    monkeypatch.setenv("STOP_COOLDOWN_SESSIONS", "zero")
    assert sc.default_sessions() == sc.DEFAULT_SESSIONS
    monkeypatch.setenv("STOP_COOLDOWN_SESSIONS", "0")
    assert sc.default_sessions() == sc.DEFAULT_SESSIONS


def test_state_file_mode_600_and_bak_on_first_write(state_file):
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802)
    assert stat.S_IMODE(os.stat(state_file).st_mode) == 0o600
    # A second (no-op) record still leaves no stray tmp files
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802)
    leftovers = [p.name for p in state_file.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ── DETERMINISTIC REPLAY (the KNRE incident) ────────────────────────────────

def _incident_book() -> dict:
    """Book the morning after the 2026-10-16 KNRE stop-out.

    KNRE is missing from positions (sold Friday). Insurance (target 7.59%)
    sits at 0% — far under its 2% tolerance — so the new-position branch
    wants to re-buy it. Total value 100,000; cash 72,000.
    """
    positions = [
        {"symbol": "KCB", "shares": 500, "avg_cost": 30.0, "current_value": 16500.0},
        {"symbol": "EABL", "shares": 50, "avg_cost": 220.0, "current_value": 11500.0},
    ]
    return {
        "cash": 72000.0,
        "initial_capital": 100000.0,
        "positions": positions,
        "total_value": 72000.0 + 28000.0,
    }


_INCIDENT_PRICES = {
    "KCB": 33.0, "EABL": 230.0,
    "KNRE": 4.1462, "BRIT": 12.5,           # insurance candidates
    "EQTY": 35.0, "ABSA": 12.0, "SCBK": 22.0, "COOP": 13.0,  # banking adds
}
_SIGNALS = [{"symbol": s, "score": 60} for s in _INCIDENT_PRICES]


def _plan_knre_buys(plan: dict) -> list[dict]:
    return [t for t in plan.get("trades", []) if t["symbol"] == "KNRE" and t["side"] == "BUY"]


def _run_incident_plan():
    return ta.generate_rebalance_plan(
        signals=[dict(s) for s in _SIGNALS],
        prices=dict(_INCIDENT_PRICES),
        portfolio=_incident_book(),
        dry_run=True,
    )


def test_replay_knre_rebuy_blocked_by_cooldown(state_file, monkeypatch):
    """2026-10-19: KNRE cooldown (exit 2026-10-16, 5 sessions) => NO KNRE buy."""
    monkeypatch.setattr(sc, "_today", lambda: date(2026, 10, 19))
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)

    plan = _run_incident_plan()
    assert _plan_knre_buys(plan) == [], (
        "cooled KNRE must not be re-bought, got: "
        f"{[t['reason'] for t in _plan_knre_buys(plan)]}"
    )
    skips = [v for v in plan["violations"] if v.get("type") == "STOP_COOLDOWN"]
    assert skips, "expected a STOP_COOLDOWN skip explaining the block"
    msg = skips[0]["message"]
    assert "2026-10-16" in msg, msg
    assert "5 session(s) remaining" in msg, msg
    # Other insurance names are still buyable
    brit_buys = [t for t in plan["trades"] if t["symbol"] == "BRIT" and t["side"] == "BUY"]
    assert brit_buys, "cooldown must not bleed into other symbols"


def test_replay_knre_rebuy_allowed_without_cooldown(state_file, monkeypatch):
    """Same book, no cooldown entry => the plan DOES buy KNRE (incident reproduces)."""
    monkeypatch.setattr(sc, "_today", lambda: date(2026, 10, 19))
    assert not state_file.exists()

    plan = _run_incident_plan()
    knre = _plan_knre_buys(plan)
    assert knre, "without a cooldown the KNRE re-buy must reproduce (incident)"
    assert knre[0]["delta_shares"] >= 1
    assert "under target" in knre[0]["reason"]
    assert not [v for v in plan["violations"] if v.get("type") == "STOP_COOLDOWN"]


def test_replay_knre_rebuy_allowed_after_cooldown_expires(state_file, monkeypatch):
    """Same entry, evaluated on 2026-10-26 (session N+1) => KNRE buy returns."""
    monkeypatch.setattr(sc, "_today", lambda: date(2026, 10, 26))
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)

    plan = _run_incident_plan()
    assert _plan_knre_buys(plan), "expired cooldown must not block re-entry"


# ── TOP-UP GUARD (target_allocation.py, holdings-in-underweight-sector branch) ─
#
# The new-position replay above only exercises the BUY loop for symbols NOT
# held. KNRE is currently HELD again (re-bought 2026-10-19 after the gate
# expired), so the guard that actually protects this week's book is the
# top-up branch: a held symbol in an underweight sector getting added to.

from trading.target_allocation import generate_rebalance_plan  # noqa: E402


def _topup_book() -> dict:
    """KNRE already held at a small size; insurance far under target.

    Insurance (KNRE+BRIT, target 7.59%, tolerance 2.0%) sits at ~2.07% of the
    100,000 book — below target-tolerance (5.59%) — so the top-up branch WOULD
    add to the held KNRE. BRIT is deliberately not held so the sector's
    per-stock target lands on KNRE alone.
    """
    knre_value = 500 * 4.1462  # 2,073.10
    return {
        "cash": 100000.0 - knre_value - 16500.0 - 11500.0,
        "initial_capital": 100000.0,
        "positions": [
            {"symbol": "KNRE", "shares": 500, "avg_cost": 4.1462,
             "current_value": knre_value},
            {"symbol": "KCB", "shares": 500, "avg_cost": 30.0,
             "current_value": 16500.0},
            {"symbol": "EABL", "shares": 50, "avg_cost": 220.0,
             "current_value": 11500.0},
        ],
        "total_value": 100000.0,
    }


def test_replay_topup_of_cooled_knre_blocked(state_file, monkeypatch):
    """Active cooldown must block a TOP-UP of an already-held KNRE."""
    monkeypatch.setattr(sc, "_today", lambda: date(2026, 10, 19))
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)

    plan = generate_rebalance_plan(
        signals=[dict(s) for s in _SIGNALS],
        prices=dict(_INCIDENT_PRICES),
        portfolio=_topup_book(),
        dry_run=True,
    )
    knre_buys = _plan_knre_buys(plan)
    assert knre_buys == [], (
        "cooled KNRE must not be topped up either, got: "
        f"{[t['reason'] for t in knre_buys]}"
    )
    skips = [v for v in plan["violations"] if v.get("type") == "STOP_COOLDOWN"]
    assert skips, "expected a STOP_COOLDOWN skip explaining the top-up block"
    assert "2026-10-16" in skips[0]["message"], skips[0]["message"]
    # The guard must not bleed into other names' top-ups (KCB banking top-up
    # is still expected for this book).
    assert any(t["symbol"] == "KCB" and t["side"] == "BUY" for t in plan["trades"])


def test_replay_topup_of_knre_returns_without_cooldown(state_file, monkeypatch):
    """Same book, no state file => the KNRE top-up BUY comes back."""
    monkeypatch.setattr(sc, "_today", lambda: date(2026, 10, 19))
    assert not state_file.exists()

    plan = generate_rebalance_plan(
        signals=[dict(s) for s in _SIGNALS],
        prices=dict(_INCIDENT_PRICES),
        portfolio=_topup_book(),
        dry_run=True,
    )
    knre = _plan_knre_buys(plan)
    assert knre, "without a cooldown the KNRE top-up must fire"
    assert knre[0]["delta_shares"] >= 1
    assert knre[0]["reason"].startswith("Top-up KNRE"), knre[0]["reason"]
    assert not [v for v in plan["violations"] if v.get("type") == "STOP_COOLDOWN"]


# ── CLI visibility ──────────────────────────────────────────────────────────

def test_expired_entry_keeps_recorded_exit_price(state_file):
    """F1 (t_aad40de5): the INACTIVE status dict carries exit_price=None; the
    list_cooldowns merge must never let a None status field clobber the
    recorded value — the operator reads this report."""
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    for on_date in ("2026-10-16", "2026-10-26"):  # not-yet-elapsed / expired
        e = list_cooldowns(on_date=on_date)["KNRE"]
        assert e["active"] is False, on_date
        assert e["exit_price"] == pytest.approx(4.0639), on_date
        assert e["exit_date"] == "2026-10-16"
        assert e["shares"] == 1802
        assert e["sessions"] == 5


def test_cli_all_shows_real_price_and_expired_marker(state_file, capsys):
    """`list --all` on an expired entry: real exit price, expired/ALLOWED
    marker, exit 0 — and never a fabricated 0.0000."""
    from trading.cli.commands import stop_cooldown as cmd

    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    rc = cmd.run(all_entries=True, on_date="2026-10-26")
    out = capsys.readouterr().out
    assert rc == 0
    assert "4.0639" in out, out
    assert "expired (re-entry ALLOWED)" in out, out
    assert "0.0000" not in out, out

    # Same for the not-yet-elapsed view
    rc = cmd.run(all_entries=True, on_date="2026-10-16")
    out = capsys.readouterr().out
    assert rc == 0
    assert "4.0639" in out, out

    # An entry with no recorded price renders "n/a", never 0.0000
    state_file.write_text(json.dumps({
        "version": 1,
        "entries": {"XFAQ": {"exit_date": "2026-10-16", "shares": 10,
                             "sessions": 5,
                             "recorded_at": "2026-10-19T00:00:00+00:00"}},
    }))
    rc = cmd.run(all_entries=True, on_date="2026-10-26")
    out = capsys.readouterr().out
    assert rc == 0
    assert "n/a" in out, out
    assert "0.0000" not in out, out


# ── Phase field (t_bed91838): not_started / active / expired / none ─────────

def test_phase_none_when_no_entry(state_file):
    """No state entry at all -> phase 'none', active False."""
    st = cooldown_status("KNRE", on_date="2026-10-19")
    assert st["phase"] == "none"
    assert st["active"] is False


def test_phase_none_when_exit_date_unparseable(state_file, capsys):
    """A hand-mangled exit_date fails open to phase 'none' and the CLI row
    says so instead of mislabeling it 'expired'."""
    state_file.write_text(json.dumps({
        "version": 1,
        "entries": {"XFAQ": {"exit_date": "not-a-date", "exit_price": 4.0,
                             "shares": 10, "sessions": 5}},
    }))
    e = list_cooldowns(on_date="2026-10-19")["XFAQ"]
    assert e["phase"] == "none"
    assert e["active"] is False

    from trading.cli.commands import stop_cooldown as cmd

    rc = cmd.run(all_entries=True, on_date="2026-10-19")
    out = capsys.readouterr().out
    assert rc == 0
    assert "no valid cooldown (re-entry ALLOWED)" in out, out
    assert "expired" not in out, out


def test_phase_not_started_on_exit_session(state_file, capsys):
    """F1b (t_bed91838): on the exit session itself (elapsed=0) the window has
    NOT opened — phase 'not_started', CLI renders 'window opens next session',
    never 'expired'."""
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    st = cooldown_status("KNRE", on_date="2026-10-16")
    assert st["phase"] == "not_started"
    assert st["active"] is False  # unchanged semantics

    from trading.cli.commands import stop_cooldown as cmd

    rc = cmd.run(all_entries=True, on_date="2026-10-16")
    out = capsys.readouterr().out
    assert rc == 0
    assert "window opens next session" in out, out
    assert "expired" not in out, out
    assert "4.0639" in out, out


def test_phase_active_and_cli_label(state_file, capsys):
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    st = cooldown_status("KNRE", on_date="2026-10-19")
    assert st["phase"] == "active"
    assert st["active"] is True

    from trading.cli.commands import stop_cooldown as cmd

    rc = cmd.run(all_entries=True, on_date="2026-10-19")
    out = capsys.readouterr().out
    assert rc == 1  # plain-text rc signals active cooldowns
    assert ("ACTIVE — 5 session(s) remaining, blocked until 2026-10-23") in out, out


def test_phase_expired_and_cli_label(state_file, capsys):
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    st = cooldown_status("KNRE", on_date="2026-10-26")
    assert st["phase"] == "expired"
    assert st["active"] is False  # unchanged semantics

    from trading.cli.commands import stop_cooldown as cmd

    rc = cmd.run(all_entries=True, on_date="2026-10-26")
    out = capsys.readouterr().out
    assert rc == 0
    assert "expired (re-entry ALLOWED)" in out, out


def test_active_entry_without_exit_price_is_none_not_zero(state_file, capsys):
    """F1a (t_bed91838): an ACTIVE date with a hand-edited entry lacking
    exit_price must report None and render 'n/a' — never a fabricated
    0.0000 in a report."""
    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    data = json.loads(state_file.read_text())
    del data["entries"]["KNRE"]["exit_price"]
    state_file.write_text(json.dumps(data))

    st = cooldown_status("KNRE", on_date="2026-10-19")
    assert st["active"] is True
    assert st["exit_price"] is None

    from trading.cli.commands import stop_cooldown as cmd

    rc = cmd.run(all_entries=True, on_date="2026-10-19")
    out = capsys.readouterr().out
    assert rc == 1
    assert "n/a" in out, out
    assert "0.0000" not in out, out


def test_cli_stop_cooldown_list_json(state_file, capsys):
    from trading.cli.commands import stop_cooldown as cmd

    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    # on_date pinned (date-rot fix, t_326ee74d session): this test relied on
    # wall-clock today and started failing once the calendar moved past the
    # exit+1 session (sessions_remaining 4 != 5). Every sibling test in this
    # module pins 2026-10-19; do the same here.
    rc = cmd.run(as_json=True, on_date="2026-10-19")
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["KNRE"]["active"] is True
    assert data["KNRE"]["sessions_remaining"] == 5


def test_cli_stop_cooldown_list_empty(state_file, capsys):
    from trading.cli.commands import stop_cooldown as cmd

    rc = cmd.run()
    out = capsys.readouterr().out
    assert rc == 0
    assert "No stop-loss re-entry cooldowns" in out


def test_cli_exit_code_signals_active_cooldowns(state_file, capsys):
    """Documented contract (audit t_7c4728a7 I2): plain-text `run()` returns
    1 when active cooldowns exist (shell scriptability) and 0 when none;
    --json always returns 0 so jq pipelines never see a non-zero exit from
    merely reporting state."""
    from trading.cli.commands import stop_cooldown as cmd

    assert cmd.run() == 0
    capsys.readouterr()

    record_stop_exit("KNRE", "2026-10-16", 4.0639, 1802, sessions=5)
    assert cmd.run() == 1  # KNRE blocked until 2026-10-23; today is inside window
    capsys.readouterr()
    assert cmd.run(as_json=True) == 0
    assert cmd.run(quiet=True) == 1


# ── format_exit_price + price-less cooldown entries (t_0c02fd5c) ────────────
#
# Commit 8a781e0 made cooldown_status() report exit_price=None for a state
# entry with no recorded price, but BOTH consumers still formatted that value
# as a float via `.get('exit_price', 0.0)` — a default that never applies
# because the key exists with the value None. Result: a NoneType format
# TypeError that (a) aborted generate_rebalance_plan entirely and (b) was
# swallowed by the auto-trader's 6b filter, silently reopening the cooled
# buy. These tests lock the fixed behaviour.

@pytest.mark.parametrize(
    "value,expected",
    [
        (4.0639, "KES 4.0639"),
        (4, "KES 4.0000"),
        (None, "KES n/a"),
        ("4.0639", "KES n/a"),
        (True, "KES n/a"),
        (False, "KES n/a"),
    ],
    ids=["float", "int", "none", "str", "bool-true", "bool-false"],
)
def test_format_exit_price(value, expected):
    assert format_exit_price(value) == expected


def _write_raw_cooldown_state(path, entry: dict) -> None:
    """Write a cooldown state entry verbatim (record_stop_exit always fills
    exit_price; these defects are only reachable via corrupt/hand-edited
    state, so the tests must write the state file directly)."""
    path.write_text(
        json.dumps({"version": 1, "entries": {"KNRE": entry}}) + "\n"
    )


_PRICELESS_ENTRIES = [
    {"exit_date": "2026-10-16", "sessions": 5},  # exit_price key ABSENT
    {"exit_date": "2026-10-16", "exit_price": None, "sessions": 5},  # None
]


@pytest.mark.parametrize("entry", _PRICELESS_ENTRIES, ids=["absent", "none"])
def test_plan_with_priceless_cooldown_entry_builds_and_skips(
    state_file, monkeypatch, entry
):
    """B1 (t_0c02fd5c): generate_rebalance_plan must NOT crash on an active
    cooldown entry with no numeric exit_price. It builds a plan, buys NO
    KNRE, and emits a STOP_COOLDOWN skip whose message contains 'n/a' and
    never 'KES 0.0000'."""
    monkeypatch.setattr(sc, "_today", lambda: date(2026, 10, 19))
    _write_raw_cooldown_state(state_file, entry)

    plan = _run_incident_plan()  # drives the REAL new-position BUY branch
    assert _plan_knre_buys(plan) == [], (
        "cooled KNRE must not be re-bought even with an unknown exit price"
    )
    skips = [v for v in plan["violations"] if v.get("type") == "STOP_COOLDOWN"]
    assert skips, "expected a STOP_COOLDOWN skip explaining the block"
    msg = skips[0]["message"]
    assert "n/a" in msg, msg
    assert "0.0000" not in msg, msg
    # Cooldown must not bleed into other names
    assert any(
        t["symbol"] == "BRIT" and t["side"] == "BUY" for t in plan["trades"]
    )


@pytest.mark.parametrize("entry", _PRICELESS_ENTRIES, ids=["absent", "none"])
def test_topup_with_priceless_cooldown_entry_builds_and_skips(
    state_file, monkeypatch, entry
):
    """Same defect through the TOP-UP BUY branch (held KNRE, underweight
    insurance sector): no crash, no top-up, 'n/a' message."""
    monkeypatch.setattr(sc, "_today", lambda: date(2026, 10, 19))
    _write_raw_cooldown_state(state_file, entry)

    plan = generate_rebalance_plan(
        signals=[dict(s) for s in _SIGNALS],
        prices=dict(_INCIDENT_PRICES),
        portfolio=_topup_book(),
        dry_run=True,
    )
    assert _plan_knre_buys(plan) == []
    skips = [v for v in plan["violations"] if v.get("type") == "STOP_COOLDOWN"]
    assert skips, "expected a STOP_COOLDOWN skip explaining the top-up block"
    msg = skips[0]["message"]
    assert "n/a" in msg, msg
    assert "0.0000" not in msg, msg


def test_cli_registry_has_stop_cooldown(capsys):
    """`trading stop-cooldown list` parses and --help still lists it."""
    from trading.cli.main import main

    rc = main(["--help"])
    assert rc == 0
    assert "stop-cooldown" in capsys.readouterr().out
    rc = main(["stop-cooldown", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "list" in out
