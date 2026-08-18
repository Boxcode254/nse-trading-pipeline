"""Trading Learning Loop — cron orchestrator.

Runs AFTER the auto-trader (scheduled 11:00 Mon–Fri). It:
  1. Ingests any NEW realised SELL outcomes from the live ledger.
  2. Aggregates them into per-symbol performance stats.
  3. Derives a learned signal-gate PROPOSAL (dry_run — never auto-applied).
  4. Prints the proposal (delivered to the trading Telegram channel by the
     cron delivery mechanism). If nothing qualifies yet, prints a quiet note.

CRITICAL SAFETY: this job NEVER writes learned_gates.json. Approval is a
manual, separate step (--approve) that a human triggers after reviewing the
proposal. This is the design the archived rule_updater demanded:
  - dry_run is hard-coded True in the engine
  - proposals route to Telegram for human approval only
  - never auto-apply

Usage:
  .venv/bin/python -m trading.learning_cron            # ingest + propose (print)
  .venv/bin/python -m trading.learning_cron --stats   # also show raw stats
  .venv/bin/python -m trading.learning_cron --approve <proposal_id>
        # PERSIST the most recent proposal as approved gates.
        # This is the ONLY path that writes learned_gates.json, and it is
        # intended to be run by a human, not by this cron job.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading import outcome_ingest as ingest_mod  # noqa: E402
from trading import learning_engine as engine      # noqa: E402


def run(*, show_stats: bool = False) -> int:
    # 1. Ingest new outcomes (idempotent via checkpoint).
    ingest_res = ingest_mod.ingest(dry_run=False)
    # 2. Aggregate + derive proposal.
    stats = engine.aggregate()
    proposal = engine.derive_gates(stats)

    print(f"🤖 **Trading Learning Loop** — {engine._now_iso()}")
    print(f"  ingested new outcomes: {ingest_res['ingested']} "
          f"(skipped {ingest_res['skipped']}, errors {len(ingest_res['errors'])})")
    print(f"  total outcomes in store: {ingest_mod.count_outcomes()}")

    if show_stats and stats:
        print("\n--- raw stats ---")
        print(json.dumps(stats, indent=2, default=str))

    print("\n" + engine.render_proposal(proposal))
    return 0


def approve(proposal_id: str) -> int:
    """Persist the latest proposal as approved gates (HUMAN-ONLY step).

    The optional proposal_id (if supplied) is only a human sanity-check that
    the right proposal is being approved; the engine actually validates by
    CONTENT against the current proposal, not by the moving timestamp id.
    Passing any non-empty string (or the right id) is accepted; the real
    guard is content-equivalence in write_approved_gates().
    """
    stats = engine.aggregate()
    proposal = engine.derive_gates(stats)
    if proposal is None:
        print("⛔ No proposal available to approve.")
        return 1
    ok = engine.write_approved_gates(proposal)
    if not ok and proposal_id:
        # write_approved_gates returns False if content no longer matches the
        # current proposal — that means the proposal shifted under us.
        print("⛔ Refused: current proposal no longer matches. Re-run "
              "learning_cron (no args) to see the live proposal, then approve.")
        return 2
    print(f"{'✅' if ok else '⛔'} Approved gates written: {ok}")
    print(json.dumps(engine.load_learned_gates(), indent=2, default=str))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Trading learning loop")
    ap.add_argument("--stats", action="store_true", help="show raw stats")
    ap.add_argument("--approve", metavar="PROPOSAL_ID",
                    help="HUMAN-ONLY: persist the latest proposal as approved gates")
    args = ap.parse_args()

    if args.approve:
        return approve(args.approve)
    return run(show_stats=args.stats)


if __name__ == "__main__":
    sys.exit(main())
