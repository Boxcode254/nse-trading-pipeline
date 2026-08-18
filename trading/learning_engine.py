"""Learning Engine — close the loop's feedback side.

Aggregates ingested realised outcomes (``outcome_ingest`` -> learning_loop.db)
into per-symbol performance stats, then derives a *learned signal-gate
override* for the rebalancer.

WHAT IT TUNES
=============
``trading.target_allocation.generate_rebalance_plan`` gates every BUY on
``signal >= 50`` and every SELL-trim-hold on ``signal >= 75``. Those two
thresholds are the leverage points learning can adjust, per symbol, from
realised win rate / consistency / drawdown.

SAFETY (hard, non-overridable in production)
============================================
* ``DRY_RUN = True`` is hard-coded. This engine NEVER mutates rebalancer
  behaviour on its own. It emits a *proposal*; a human must approve it
  via Telegram before it is written to ``learned_gates.json`` (the file
  the rebalancer reads). The cron job prints the proposal and delivers it
  to the trading channel — no auto-apply, ever.
* Fail-open: if learning_loop.db is empty, corrupt, or missing, the
  engine returns ``None`` and the rebalancer falls back to its base
  gates (50 / 75). Learning can only NARROW or WIDEN within safe bounds;
  it can never disable a gate or set it to a value that would force trades.
* Bounds: learned gate clamped to [35, 70] for BUY and [60, 90] for SELL
  trim-hold. A symbol needs a MIN_SAMPLES (default 3) realised outcomes
  before any tuning applies; below that it stays at base.

OUTPUT CONTRACT
===============
``derive_gates()`` returns either ``None`` (no learning available) or:

    {
      "generated_at": ISO,
      "per_symbol": {SYM: {"buy_gate": f, "sell_hold_gate": f,
                            "samples": int, "win_rate": f,
                            "avg_pnl_pct": f, "consistency": f}},
      "any": {SYM-or-"ANY": {"buy_gate": f, "sell_hold_gate": f}},
      "dry_run": True,
      "proposal_id": "<short>",
    }

The rebalancer consumes ``any`` (merged with per_symbol) via
``load_learned_gates()``. ``write_approved_gates()`` is the ONLY function
that persists; it is called manually after human approval, never by cron.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[1]
import sys
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading.outcome_ingest import LEARNING_DB, _init_db  # noqa: E402

# File the rebalancer reads at plan time. Only written by write_approved_gates().
GATES_FILE = Path.home() / ".trading" / "learned_gates.json"

# ── Safety constants ───────────────────────────────────────────────────────
DRY_RUN = True  # hard-coded; never auto-applied by the engine itself
BASE_BUY_GATE = 50.0
BASE_SELL_HOLD_GATE = 75.0
MIN_SAMPLES = 3
BUY_GATE_BOUNDS = (35.0, 70.0)
SELL_GATE_BOUNDS = (60.0, 90.0)
# A symbol with a win rate below this gets its BUY gate RAISED (more cautious);
# above this, gate can be LOWERED (act sooner on decent names). Range 0..1.
WIN_RATE_LOW = 0.45
WIN_RATE_HIGH = 0.65
# Max single-step change from base, so learning nudges, never lurches.
MAX_STEP = 12.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def aggregate() -> dict[str, dict[str, Any]]:
    """Return {symbol: stats} from realised outcomes. Empty dict if none."""
    try:
        with sqlite3.connect(LEARNING_DB) as conn:
            _init_db(conn)
            rows = conn.execute(
                """
                SELECT symbol, realised_pnl, pnl_pct, shares, exit_price
                FROM realized_outcomes
                ORDER BY symbol, exit_timestamp
                """
            ).fetchall()
    except sqlite3.Error:
        return {}

    by_sym: dict[str, list[tuple]] = {}
    for r in rows:
        by_sym.setdefault(r[0], []).append(r)

    stats: dict[str, dict[str, Any]] = {}
    for sym, recs in by_sym.items():
        pnls = [float(x[1]) for x in recs]
        pcts = [float(x[2]) for x in recs if x[2] is not None]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / n if n else 0.0
        avg_pct = sum(pcts) / len(pcts) if pcts else 0.0
        win_rate = wins / n if n else 0.0
        # Consistency: 1 - (stdev / (|mean| + epsilon)), clamped 0..1.
        if n >= 2 and abs(avg_pnl) > 1e-9:
            mean = avg_pnl
            var = sum((p - mean) ** 2 for p in pnls) / n
            std = var ** 0.5
            consistency = _clamp(1.0 - (std / (abs(mean) + 1e-6)), 0.0, 1.0)
        else:
            consistency = 0.0
        stats[sym] = {
            "samples": n,
            "wins": wins,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "avg_pnl_pct": round(avg_pct, 4),
            "consistency": round(consistency, 4),
        }
    return stats


def derive_gates(stats: Optional[dict[str, dict[str, Any]]] = None) -> Optional[dict[str, Any]]:
    """Derive a learned-gate proposal from aggregated stats.

    Returns None when there is nothing to learn (no symbol has MIN_SAMPLES
    realised outcomes). Otherwise returns the proposal dict documented in
    the module docstring.
    """
    if stats is None:
        stats = aggregate()

    per_symbol: dict[str, dict[str, Any]] = {}
    any_block: dict[str, dict[str, float]] = {}

    for sym, s in stats.items():
        if s["samples"] < MIN_SAMPLES:
            continue
        # Tune BUY gate from win rate + consistency.
        #   low win rate -> raise gate (wait for stronger signal)
        #   high win rate + consistent -> lower gate (act sooner)
        delta = 0.0
        if s["win_rate"] < WIN_RATE_LOW:
            delta += (WIN_RATE_LOW - s["win_rate"]) * 40.0  # up to +~22
        elif s["win_rate"] > WIN_RATE_HIGH:
            delta -= (s["win_rate"] - WIN_RATE_HIGH) * 30.0  # down to ~-10
        # Consistency modifier: inconsistent -> slightly more cautious.
        delta += (0.5 - s["consistency"]) * 8.0
        delta = _clamp(delta, -MAX_STEP, MAX_STEP)

        buy_gate = _clamp(BASE_BUY_GATE + delta, *BUY_GATE_BOUNDS)
        # SELL-trim-hold gate: mirror logic (weak names we hold longer before
        # trimming when they've been profitable; trim sooner when they bleed).
        sell_hold_gate = _clamp(
            BASE_SELL_HOLD_GATE - delta * 0.5, *SELL_GATE_BOUNDS
        )

        per_symbol[sym] = {
            "buy_gate": round(buy_gate, 2),
            "sell_hold_gate": round(sell_hold_gate, 2),
            "samples": s["samples"],
            "win_rate": s["win_rate"],
            "avg_pnl_pct": s["avg_pnl_pct"],
            "consistency": s["consistency"],
        }
        any_block[sym] = {
            "buy_gate": round(buy_gate, 2),
            "sell_hold_gate": round(sell_hold_gate, 2),
        }

    if not per_symbol:
        return None

    return {
        "generated_at": _now_iso(),
        "per_symbol": per_symbol,
        "any": any_block,
        "dry_run": DRY_RUN,
        "proposal_id": _now_iso().replace("-", "").replace(":", "").replace("T", "_").replace("+", "_")[:17],
    }


def load_learned_gates() -> dict[str, dict[str, float]]:
    """Read the APPROVED gates file the rebalancer consumes.

    Fail-open: missing/corrupt => empty dict (rebalancer uses base gates).
    """
    if not GATES_FILE.exists():
        return {}
    try:
        d = json.loads(GATES_FILE.read_text())
        any_block = d.get("any", {})
        if not isinstance(any_block, dict):
            return {}
        # Normalise and clamp defensively (a corrupt approval is still bounded).
        out: dict[str, dict[str, float]] = {}
        for sym, g in any_block.items():
            if not isinstance(g, dict):
                continue
            bg = _clamp(float(g.get("buy_gate", BASE_BUY_GATE)), *BUY_GATE_BOUNDS)
            shg = _clamp(float(g.get("sell_hold_gate", BASE_SELL_HOLD_GATE)), *SELL_GATE_BOUNDS)
            out[sym] = {"buy_gate": bg, "sell_hold_gate": shg}
        return out
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return {}


def write_approved_gates(proposal: dict[str, Any]) -> bool:
    """PERSIST an approved proposal. Called ONLY after human Telegram approval.

    Never called by the cron job. Returns True on success.
    """
    if not isinstance(proposal, dict) or "any" not in proposal:
        return False
    if not proposal.get("any"):
        return False
    # Guard against approving a proposal whose gates differ from the current
    # one in the store (defends the human against approving a stale/changed
    # proposal). Compare by content, not by the moving proposal_id timestamp.
    current = derive_gates()
    if current is None:
        return False
    cur_any = current.get("any", {})
    prop_any = proposal.get("any", {})
    if set(cur_any.keys()) != set(prop_any.keys()):
        return False
    for sym, g in prop_any.items():
        cg = cur_any.get(sym, {})
        if (round(float(g.get("buy_gate", 0)), 2) != round(float(cg.get("buy_gate", -1)), 2)
                or round(float(g.get("sell_hold_gate", 0)), 2) != round(float(cg.get("sell_hold_gate", -1)), 2)):
            return False
    payload = {
        "generated_at": proposal.get("generated_at", _now_iso()),
        "approved_at": _now_iso(),
        "dry_run": False,
        "proposal_id": proposal.get("proposal_id", "manual"),
        "any": {sym: {
            "buy_gate": _clamp(float(g.get("buy_gate", BASE_BUY_GATE)), *BUY_GATE_BOUNDS),
            "sell_hold_gate": _clamp(float(g.get("sell_hold_gate", BASE_SELL_HOLD_GATE)), *SELL_GATE_BOUNDS),
        } for sym, g in proposal["any"].items()},
        "per_symbol": proposal.get("per_symbol", {}),
    }
    GATES_FILE.write_text(json.dumps(payload, indent=2))
    return True


def render_proposal(proposal: Optional[dict[str, Any]]) -> str:
    """Human-readable proposal for Telegram delivery."""
    if proposal is None:
        return ("🤖 **Trading Learning — no proposal**\n"
                f"⏰ {_now_iso()}\n"
                "No symbol has enough realised outcomes (min "
                f"{MIN_SAMPLES}) to tune yet. Loop is ingesting; "
                "keep feeding it.")
    lines = [
        "🤖 **Trading Learning — GATE PROPOSAL (DRY RUN, needs approval)**",
        f"⏰ {_now_iso()}",
        f"proposal_id: `{proposal['proposal_id']}`",
        "",
        f"{'SYM':5} {'n':>3} {'win%':>6} {'avg%':>7} {'consist':>8} → BUY≥  HOLD≥",
    ]
    for sym, g in sorted(proposal["per_symbol"].items()):
        lines.append(
            f"{sym:5} {g['samples']:>3} {g['win_rate']*100:>5.0f}% "
            f"{g['avg_pnl_pct']:>6.1f}% {g['consistency']:>7.2f} "
            f"→ {g['buy_gate']:>5.0f}  {g['sell_hold_gate']:>5.0f}"
        )
    lines.append("")
    lines.append("**To apply:** review, then run the approver with this "
                 "proposal_id. Auto-apply is disabled by design.")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Derive learned gate proposal")
    ap.add_argument("--render", action="store_true", help="print proposal")
    ap.add_argument("--stats", action="store_true", help="print raw stats")
    args = ap.parse_args()

    st = aggregate()
    if args.stats:
        print(json.dumps(st, indent=2, default=str))
    prop = derive_gates(st)
    if args.render:
        print(render_proposal(prop))
    else:
        print(json.dumps(prop, indent=2, default=str))
