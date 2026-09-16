# NSE Trading Platform — Remediation Completion Report

**Date:** 2026-09-15 (EAT)
**Author:** Hermes (default/orchestrator profile)
**Scope document:** `audit/platform-review-and-remediation-2026-09-15.md` (findings TP-001 … TP-010)
**Status:** ALL PLAN FINDINGS CLOSED. Platform frozen for strategy changes until the 2026-11-04 evaluation gate.

---

## 1. Outcome summary

Five sequential engineering tasks closed all nine remediation findings and rebuilt the
test suite to a fully green state. Every task was independently verified before the next
started; four of five required direct orchestrator intervention (auditor harness crashes,
runtime-cap timeouts, uncommitted work).

| Metric | Before (2026-09-15 morning) | After (2026-09-15 evening) |
|---|---|---|
| Full test suite | 359 passed / **2 failed** (chronic) | **466 passed / 0 failed** |
| Deselectable live-data tests | n/a | 3 marked, deselect clean (`-m "not live_data"`: 463/0) |
| Portfolio numbers | 2 read models, KES 95.53 apart | 1 canonical model, exact tie-out |
| Gate verdict | fake-precise "-8.31pp" from mismatched dates | `incomparable` with machine-readable reasons |
| Order status | 18 FILLED orders reported "open" | 0 open (state machine fixed) |
| Win-rate claim | "100.0% success" from 4 of 301 outcomes | outcome-gated, `insufficient_sample` below 30 |
| Public CLIs | 2 contradictory + 1 crashing + 1 "coming soon" | 1 canonical Typer CLI, all help commands functional |
| Suspended BAMB | scored, forecast, 1.93% target allocation | `non_tradable` everywhere, zero investable surfaces |
| Sector momentum uplift | silently dead (swallowed TypeError) | working, freshness-gated, inspectable diagnostics |

## 2. Commit ledger (all pushed to `Boxcode254/nse-trading-pipeline`, verified 0/0)

| Commit | Task | Findings | What changed |
|---|---|---|---|
| `fdacd0f` | 1 | TP-001, TP-002 | Order lifecycle classification (OPEN/TERMINAL were enum members; `str` mixin made `"FILLED" in OPEN` a substring test → 18 fills reported open). Sector momentum uplift restored: `Path` coercion, 7-day freshness gate, `sector_momentum_diagnostics()` with per-member states (missing/malformed/stale), no silent swallowing. Banking cap now correctly `{warn:40, hard:55}` live. |
| `d5d6c39` | 2 | TP-003, TP-004, TP-007 | New `trading/tradability.py` — single tradability predicate (static `SUSPENDED_SYMBOLS` + dynamic OHLC-lock detector). BAMB returns `status: non_tradable` with no score/forecast/horizon; excluded from opportunities, decision targets, benchmark universes. CLI consolidated: broken `execute plan/deploy` removed, `allocations` alias to real targets, `dashboard` rebuilt on `mtm_state.json`, legacy `python -m trading` delegates with name translation (`run→morning`, `rank→opportunities`, `allocate→target`). Mandate made explicit: "NSE equity allocator — ~10% cash reserve (paper)"; gold/forex/T-bills labelled BROADER WEALTH-ALLOCATION CONTEXT (NOT EXECUTABLE) and structurally separated (`mandate` vs `wealth_context`). `target --verify` renamed Contract-Consistency Check (structural, not independent agreement). |
| `61edf12` | 3 | TP-005 | New `trading/services/benchmark_compare.py`: portfolio and benchmark valued at the SAME timestamp via the SAME resolver over an explicitly versioned universe (`benchmark_universe_version`). Verdict is `comparable` or `incomparable` with reasons (`benchmark_record_stale`, `universe_mismatch`, `unpriced_symbols`) — never a precise gap from mismatched dates. `portfolio_status.py` extended to the canonical read model with per-position provenance (AXYS>feed>CSV), valuation basis, price date. `_cron_full_report.py` reads the canonical model; staleness computed; INCOMPARABLE renders BLOCKED with reasons and the 2026-11-04 deadline. |
| `bd303eb` | 4 | TP-006, TP-008 | Honest metrics: `outcome_performance` block (win_rate = wins/evaluated only at ≥30 closed outcomes, with numerator/denominator/coverage/unresolved), hardcoded `avg_confidence`/`best_strategy` removed (evidence-derived or `unavailable:`), monthly report renders `insufficient_sample (evaluated=X of Y, Z% coverage)` + raw counts instead of success claims; MoM and retirement sections gated. Integrity reporting: new `trading/services/integrity_status.py` maintains `portfolio/integrity_status.json` (checked_at/status/source/divergences; `last_failure` preserved across ok runs); report renders CURRENT status vs last recorded failure with computed ages; log-tail replay and hardcoded dates removed. Also fixed: `human_age` double-rounding, `monthly_stats` view `strftime('%%Y-%%m')` literal-bucket bug (all months collapsed into one row in production), gate/caveat contract conformance. |
| `dbc649e` | 5 | TP-009 + carried gap | Test isolation: chronic live-data assertions converted to read-only invariant tests marked `live_data` (deselectable). Ledger test now checks parse/field/FIFO-replay-share-safety invariants, not a hardcoded 82-count; BAMB test is conditional-if-present, not mandatory. New `trading/tests/test_tradability.py` — 23 deterministic fixture tests for the predicate (static suspension, dynamic lock, normalisation, None/empty inputs, universe exclusion). |

Earlier same-day commits also pushed: `9d23050` (learning outcome dedupe), `54b00b5` (pnl_pct propagation + 23-row backfill).

## 3. Task execution record

| Task | Card | Assigned | Outcome |
|---|---|---|---|
| 1 | `t_d70a55de` | engineer (+auditor attempt) | Engineer fixed; auditor judge died (BadRequestError); engineer review approved; default re-verified all 8 items → APPROVED |
| 2 | `t_bbb3d957` | engineer | Completed but left uncommitted; default verified live and committed `d5d6c39` |
| 3 | `t_09c837ee` | engineer | Completed cleanly; default re-verified (tie-out, incomparable verdict, 10/10 focused tests) |
| 4 | `t_24e496d2` | engineer | Timed out at 30m cap (~90% done); default finished, fixed 3 bugs, committed `bd303eb`, closed APPROVED |
| 5 | `t_708f6d0a` | engineer | Timed out at 25m cap (work complete, uncommitted); default verified (466/0) and committed `dbc649e` |

**Operational lessons encoded:**
- Auditor worker is chronically flaky (pid-not-alive crashes, judge BadRequestError). Protocol recorded in `post-engineering-audit` skill: when an auditor card auto-blocks, default runs the verification items directly with the same output contract and closes the card with honest provenance.
- Engineer runtime cap: 30m is too generous for last-mile fixes; 25m still tight for test-heavy tasks. Expect uncommitted work on timeout — always check `git status` before re-dispatching.
- Goal-mode judge errors are harness failures, not credit exhaustion — check the run log before blaming the model.

## 4. System state after remediation

**Trust surfaces**
- One canonical CLI (`trading`); every advertised command runs; one canonical dashboard/report path; one canonical read model (`portfolio_status.py`).
- One tradability predicate (`tradability.py`) consumed by signals, ranking, forecast, decision, benchmarks.
- One comparison authority (`benchmark_compare.py`) that refuses unestablishable gap claims.

**Gate standing (as of close)**
- Stage-1 gate: **INCOMPARABLE** — benchmark record 6+ days stale, universe mismatch (legacy eqw-v1: 14 assets incl. BAMB/EQTY/forex/WTK vs 9-position NSE book), unpriced symbols. Deadline **2026-11-04**. A verdict requires a same-date, same-universe, net-of-cost comparison — the machinery now exists and blocks nonsense instead of publishing it.
- Live book: KES 105,275.28 (9 positions), cash 21.9%, strategy +5.28% gross vs the stale benchmark's +10.59% — no honest verdict is possible on those numbers, which is the point.

**Constraints preserved through all five tasks (verified per task)**
- Price authority AXYS > feed > CSV; RunLock; production-write guard; idempotency; circuit breaker; safety gate; reconciliation; POLICY.md news prohibition; no production runtime writes by tests; local commits only.

## 5. What remains (deliberately not done)

1. **TP-010 — read-only decision cockpit** (P2): the single-screen product surface. Spec is in the review document §4. Gated on everything above — it is, and can now be built on trustworthy foundations. Estimated 3–5 days.
2. **Benchmark modernisation**: build started 2026-09-15 (Task 6, dispatched). Universe definition approved by Kratos the same day: dual versioned benchmarks — `eqw-v2-held` (9 held names) and `eqw-v2-mandate` (tradable STRATEGY members) — tracked daily at market close. The decision rule that consumes them is pre-registered in **`audit/stage1-gate-preregistration-2026-09-15.md`** (GO requires: net return ≥ 0, ≥ eqw-v2-held, max drawdown < 15%, ≥20-session window; INSUFFICIENT_WINDOW defers once to 2026-12-01; anti-goalpost clause fixed after 2026-10-01).
3. **Operator notes from Task 1** (parked, need decisions): BRIT has no price CSV (insurance sector momentum reports `data_unusable`); `services` (WTK) has no tier in `EXECUTION_CONFIG["sector_caps"]` (falls to default cap). Config-coherence items, not defects. **Addressed by Task 7 (2026-09-15)**: absent members are now skipped instead of making a sector `data_unusable`, and `services` carries an explicit `{warn: 15, hard: 20}` tier. Residual: insurance has only two members, so with BRIT absent coverage is 1 of 2 and the uplift is still withheld (`insufficient_member_coverage`) until BRIT has a price CSV.
4. **Tradability short-window rule**: members with < lookback+1 rows contribute shorter windows to momentum (pre-existing, unchanged, no live impact today). Fold into a future config pass if desired.
5. **4 Nov gate**: strategy stays frozen. No tuning before the verdict.
6. **WTK disposition (recorded 2026-09-15, approved by Kratos)**: orphan outside STRATEGY targets — no further purchases; exit candidate at the 2026-11-04 gate.

## 6. Frozen-state instruction

Until 2026-11-04: correctness and safety repairs only. No strategy, threshold, weight, or universe changes. Any proposed change goes through Emmanuel first.
