# NSE Trading Platform Review and Remediation Plan

**Date:** 2026-09-15 (EAT)  
**Owner:** Emmanuel / Kratos  
**Review author:** Hermes, default/orchestrator profile  
**Runtime:** `/home/hermes/.trading`  
**Revenue category:** `[REV:infra]`, supporting a revenue-soon portfolio product  
**Decision:** Keep the system paper-only. Correct trust and product-coherence defects before adding new trading features or presenting the platform as proven.

## 1. Executive verdict

The project is a serious, NSE-specific investment research and paper-trading system. Its strongest qualities are local-market adaptation, price provenance, execution safeguards, realistic costs, auditability, and automated operation.

It is currently:

- a credible private research and paper-trading system;
- not yet one coherent customer-facing platform;
- not ready for autonomous real-money trading;
- not yet proven to outperform a passive benchmark.

The principal problem is no longer feature scarcity. It is **trust and product coherence**: user-facing commands disagree, some metrics are mislabeled, performance comparisons mix dates/universes, a suspended counter remains visible as investable, and two verified code defects make risk/status output incorrect.

### Review scorecard

| Area | Score | Basis |
|---|---:|---|
| NSE domain design | 8/10 | Local fees, liquidity, suspended symbols, sector rules, AXYS close reconciliation |
| Safety and governance | 7/10 | State machine, idempotency, breakers, emergency stop, production-write guard; verified status defect remains |
| Data provenance | 8/10 | AXYS > feed > CSV authority chain with provenance |
| Architecture and test breadth | 7/10 | Service/CLI separation and broad tests, but duplicate/stale public surfaces |
| Product coherence and UX | 4/10 | Multiple contradictory entry points and an archived dashboard exposed as live |
| Evidence of trading edge | 3/10 | Strategy currently behind a stale/non-comparable benchmark; outcome coverage is insufficient |
| Real-money readiness | 3/10 | Paper execution only; trust blockers and evaluation gate remain |

## 2. Verified strengths to preserve

### 2.1 NSE-specific design

The platform accounts for sector concentration, thin liquidity, suspended counters, NSE costs, delayed/EOD data, broker-published closes, position limits, drawdowns, and cash reserves. This is materially stronger than a generic indicator bot with NSE symbols added.

### 2.2 Price authority and provenance

`trading/price_source.py:28-47` defines the authority chain:

1. AXYS official close;
2. intraday/feed price;
3. cached CSV close;
4. unresolved, with an explicit fallback at the caller.

Preserve this ordering and provenance in every new consumer.

### 2.3 Execution safeguards

Preserve all of the following during remediation:

- production-write guard: `trading/execution/engine.py:100-126`;
- idempotency before broker submission: `trading/execution/engine.py:172-192`;
- safety gate: `trading/execution/engine.py:194-216`;
- circuit breaker and resilient broker call: `trading/execution/engine.py:218-280`;
- reconciliation: `trading/execution/engine.py:282-301,342-411`;
- explicit order transitions: `trading/execution/models.py` and `order_store.py`;
- emergency stop, drawdown halt, position caps, sector caps, and realistic cost model.

### 2.4 Economic realism

`trading/config.py:301-316` models 1.5% per-side costs, a KES 60 minimum commission, 0.15% estimated slippage, and fee headroom. Preserve a single cost source of truth.

### 2.5 Policy boundary

`POLICY.md:13-45` correctly limits news to context/alerting and forbids it as an execution input. No remediation task may wire news or LLM sentiment into execution.

### 2.6 Live health baseline

Verified on 2026-09-15:

- `trading doctor --json`: `status=healthy`, `health_score=100`, market data `14/14 pairs OK`;
- key no-agent trading crons are enabled and their latest listed status is `ok`;
- target-allocation NSE-only agreement command returned `verified=true`, `agreed=true`;
- runtime state is excluded from git; the private remote is `Boxcode254/nse-trading-pipeline`;
- full suite baseline: **359 passed, 2 failed** (`tests/` + `trading/tests/`).

A 100/100 doctor score is component health, not product correctness. The doctor does not currently detect the findings below.

## 3. Findings and required remediations

Severity meanings:

- **P0:** trust/correctness blocker; fix before relying on the affected output;
- **P1:** product coherence or evidence-quality defect;
- **P2:** productization improvement after correctness is restored.

### TP-001 — P0 — FILLED orders are reported as open

**Evidence**

- `trading/execution/models.py:41-45` defines `TERMINAL` and `OPEN` inside `OrderStatus`.
- Runtime introspection showed they became enum members rather than ordinary frozensets.
- Verified behavior:

```text
PENDING open=True terminal=False
NEW open=True terminal=False
PARTIALLY_FILLED open=True terminal=False
FILLED open=True terminal=True
REJECTED open=False terminal=True
CANCELLED open=False terminal=True
```

- `trading execute status --json` listed 18 `open_orders`; inspection of the canonical order store showed all 18 were `FILLED`.
- `OrderStore.list_open()` depends on `is_open()` at `trading/execution/order_store.py:216-228`.

**Required outcome**

- `OPEN_STATUSES` and `TERMINAL_STATUSES` must be ordinary module-level immutable sets, not enum members.
- Open means only `PENDING`, `NEW`, `PARTIALLY_FILLED`.
- Terminal means only `FILLED`, `REJECTED`, `CANCELLED`.
- `OrderStatus` must contain only real broker lifecycle values.
- Existing terminal order files must remain unchanged; this is a read/classification repair.

**Acceptance**

- Unit tests cover every status through class helpers and module helpers.
- Temporary-store test proves `list_open()` excludes all terminal records.
- Read-only live smoke shows zero open orders when the store contains only terminal orders.

### TP-002 — P0 — Sector momentum hard-cap uplift is silently disabled

**Evidence**

- `trading/config.py:55-59` defines `DATA_DIR` as `str`.
- `trading/config.py:442-464` evaluates `DATA_DIR / f"nse_{sym}.csv"`, raising `TypeError`.
- The broad `except Exception` swallows the error and returns the base cap.
- Verified runtime output: `sector_cap('banking') == {'warn': 40.0, 'hard': 45.0}` even though independently read 20-session CSV returns averaged approximately +15.01% and should qualify under `momentum_min_pct=0.0`.

**Required outcome**

- Use `Path(DATA_DIR)` or make path constants consistently typed.
- Do not silently swallow path/type errors. Missing/invalid data may fail conservatively, but diagnostics must identify why no uplift was applied.
- Add a freshness contract before applying momentum. A stale CSV must not grant a hard-cap uplift.
- Do not tune thresholds or change sector targets in this repair.

**Acceptance**

- Deterministic fixture with fresh positive momentum applies exactly one configured uplift.
- Fresh negative momentum applies none.
- Missing/malformed/stale files apply none and surface an inspectable reason or warning.
- Existing tiered sector caps remain otherwise unchanged.

### TP-003 — P0 — Suspended BAMB remains visible as investable

**Evidence**

- `BAMB` remains in `PAIRS`, `YFINANCE_TICKERS`, `ASSET_CATEGORIES`, and `ASSET_DISPLAY_ORDER` in `trading/config.py`.
- It is correctly in `SUSPENDED_SYMBOLS` and blocked from auto-execution.
- Fresh commands still emitted an investment score, `Reduce`, an expected 24-month horizon, and a 1.93% target in the ordinary decision surface.
- The daily supervision report listed BAMB under assets to avoid as if it were an active security.

**Required outcome**

- Establish one central tradability predicate/universe.
- Suspended symbols may remain visible only as legacy/non-tradable holdings or monitoring records.
- They must be excluded from new signals, rankings, forecasts, opportunity lists, active target allocations, and newly computed active benchmark universes.
- If a user queries BAMB directly, return a clear non-tradable/suspended response rather than a forecast, score, holding period, or action.
- Do not delete or rewrite historical transactions, holdings, snapshots, or runtime portfolio state.

### TP-004 — P1 — Public command surfaces contradict each other

**Evidence**

- `trading` invokes the modern Typer CLI through `/home/hermes/.local/bin/trading`.
- `python3 -m trading` invokes an older argparse CLI from `trading/__main__.py`.
- `trading dashboard` exposes an archived dashboard (`trading/dashboard.py:1-10`).
- `trading allocations --json` returns `status=coming_soon`, despite target allocation already being implemented.
- `trading execute plan --json` crashes: `AttributeError: module 'trading.cli.commands.execute' has no attribute 'plan_cmd'`.
- `trading/cli/main.py:461-476` registers `plan` and `deploy` callbacks that do not exist in the command module.

**Required outcome**

- One canonical public CLI and help tree.
- `python -m trading` and `trading` must reach the same implementation, or the legacy entry point must clearly and safely delegate.
- No command advertised in `--help` may crash because its callback is absent.
- Remove obsolete placeholder commands, or make them explicit aliases to the implemented canonical functionality.
- Replace the archived dashboard public surface with a canonical current read-only summary, or remove it from help until it is real.

### TP-005 — P1 — Portfolio and benchmark output mix sources and dates

**Evidence**

Fresh outputs differed:

- `trading portfolio show --json`: KES 105,179.75;
- `portfolio/mtm_state.json`: KES 105,275.28;
- difference: KES 95.53.

The daily report compared a 2026-09-14 MTM portfolio with a benchmark last snapped on 2026-09-09. It nevertheless emitted a precise gate:

- strategy +5.28%;
- benchmark +10.59%;
- gross gap -5.31 percentage points;
- reported net gap after 3%: -8.31 percentage points.

The benchmark definition also contains forex and BAMB while the current live portfolio is a nine-position NSE book.

**Required outcome**

- Define one canonical read model for portfolio total, positions, and price provenance.
- Generate portfolio and benchmark values from the same as-of timestamp, price resolver, eligible universe, cash assumptions, and cost policy.
- If comparability cannot be established, block the verdict and return `comparison_status=incomparable` with reasons. Never publish a precise gap from mismatched dates.
- Make the active benchmark universe explicit and versioned. Preserve historical benchmark records; do not rewrite them silently.

### TP-006 — P1 — Performance reporting overclaims learning

**Evidence**

- `logs/monthly_report-2026-09.md` reports 301 recommendations, only 4 evaluated outcomes, and a 100% success rate.
- Evaluated coverage is **1.33%**.
- The prose says the system is producing profitable signals more often than not, which is unsupported by four evaluated outcomes.
- `trading/services/stats.py:69-105` labels BUY/all-signal share as `win_rate_pct`, BUY/(BUY+SELL) as `actionable_win_rate_pct`, hardcodes `avg_confidence=0.0`, and hardcodes Strategy A as best.

**Required outcome**

- Remove or rename metrics that do not measure outcomes.
- Outcome performance must expose numerator, denominator, evaluated coverage, eligible sample, unresolved sample, and confidence caveat.
- Do not call a result `win rate` unless wins and losses come from evaluated outcomes.
- No positive performance conclusion below 30 closed eligible outcomes; render `insufficient_sample` instead.
- Persist and calculate confidence if the product displays average confidence; otherwise omit it.
- A best strategy must be evidence-derived or labeled unavailable.

### TP-007 — P1 — “Agreement” is structural rather than independent

**Evidence**

- `trading/target_allocation.py:978-987` says NSE-only agreement is structural because the Decision Engine sources the same target allocation.
- Ordinary `trading decision --json` describes a broader multi-asset allocation including equities, forex, gold, T-bills, and cash.
- The auto-trader mandate is approximately 90% NSE equities and 10% cash.

**Required outcome**

- Make the primary mandate explicit: **NSE equity allocator with a 10% cash reserve**.
- Label broader gold/forex/T-bill output as separate wealth-allocation context unless it is tracked and executable.
- Rename structural agreement to a contract-consistency check; do not present it as independent model agreement.

### TP-008 — P1 — Daily integrity report surfaces an old failure as current

**Evidence**

- `scripts/_cron_full_report.py:68-73` reads the last two non-empty lines of `portfolio/book_integrity.log`.
- That log contains only the 2026-08-27 TOTL divergence.
- The 2026-09-14 daily report therefore displayed the old failure in its current integrity section even though later integrity cron runs list `ok`.
- `scripts/_cron_full_report.py:142-145` also hardcodes “benchmark snapshot is 2d stale” even when the measured gap was five days.

**Required outcome**

- Persist a current integrity status record with `checked_at`, `status`, `source`, `divergences`, and optional `last_failure`.
- The daily report must distinguish current status from historical incident.
- Compute freshness age; never hardcode it.
- Remove fixed historical text such as “Cross-source reconciliation ran 2026-08-27.”

### TP-009 — P1 — Tests depend on mutable production state

**Evidence**

Full suite baseline: **359 passed, 2 failed**.

- `trading/tests/test_audit005_backtest_metrics.py:102-113` asserts the live transaction ledger has exactly 82 entries; it now has 94.
- `trading/tests/test_target_allocation_bamb.py:120-129` asserts live state still contains exactly 39 BAMB shares; the current live portfolio no longer has BAMB.

**Required outcome**

- Deterministic tests use temporary fixtures, not mutable production ledgers/state.
- Live integration checks are read-only, assert invariants rather than historical counts, and are separately marked.
- Add smoke tests that invoke every command advertised by `trading --help` in a safe fixture/dry-run context.
- Final full suite must pass without writing production state.

## 4. Product improvement after trust repair

### TP-010 — P2 — Build one read-only decision cockpit

Do not start this until TP-001 through TP-009 are verified.

The primary screen should answer:

1. What is my portfolio worth now?
2. Am I ahead or behind a comparable benchmark?
3. What changed today?
4. What, if anything, should I do?
5. Can I trust the data and pipeline?

Minimum sections:

- portfolio value, net return, and drawdown;
- same-date benchmark comparison or an explicit incomparable state;
- positions with price source and as-of timestamp;
- actionable trades plus explicit skip reasons;
- signal, strategic target, current weight, and action shown separately;
- sector concentration and current risk breaches;
- latest successful pipeline stages and data freshness;
- 2026-11-04 evaluation-gate countdown;
- unmistakable PAPER / REAL-MONEY mode badge.

Example decision unit:

```text
KCB
Signal: Hold, 71/100
Strategic target: 12.2%
Current weight: 11.8%
Action today: No trade
Reason: Drift is below the minimum economical trade threshold
Price: KES X.XX, AXYS close, as of YYYY-MM-DD HH:MM EAT
```

Keep research diagnostics, raw indicators, and infrastructure details behind secondary views.

## 5. Sequential engineer plan

Only one engineer task should run at a time. Each task must stop after its named slice and receive independent verification before the next begins.

| Task | Scope | Findings | Estimate |
|---|---|---|---:|
| 1 | Execution-state and sector-risk correctness | TP-001, TP-002 | 2–4 hours |
| 2 | Tradability and CLI surface consolidation | TP-003, TP-004, TP-007 | 4–6 hours |
| 3 | Canonical valuation and benchmark comparison | TP-005 | 4–6 hours |
| 4 | Honest metrics and current-status reporting | TP-006, TP-008 | 3–5 hours |
| 5 | Test isolation and final contract coverage | TP-009, all regression gates | 3–5 hours |
| 6, optional after gate | Read-only decision cockpit | TP-010 | 3–5 days |

## 6. Global engineering constraints

These apply to every task:

1. **Paper-only.** Do not enable real broker execution.
2. **No production runtime writes during tests.** Do not modify `portfolio/state.json`, `portfolio/transactions.json`, `portfolio/mtm_state.json`, `execution/*.json`, live caches, or live DBs.
3. Use temporary directories/fixtures for mutation tests.
4. Preserve price authority: AXYS > feed > CSV.
5. Preserve the news execution prohibition in `POLICY.md`.
6. Preserve RunLock, production-write guard, idempotency, state machine, circuit breaker, safety gate, and reconciliation.
7. Do not tune strategies, thresholds, weights, or the 2026-11-04 evaluation gate while repairing correctness.
8. Do not rewrite historical evidence to make current metrics look better.
9. The worktree is dirty. Touch and commit only named task files. Never include unrelated modifications or runtime artifacts.
10. Local commit only. Do not push without Emmanuel’s explicit instruction.
11. Every command advertised in help must be tested safely.
12. “Tests passed” is not sufficient: verify each finding’s stated intent and read-only live output where applicable.

## 7. Required verification gates

After each engineering task:

1. Review the exact diff and changed-file list.
2. Re-run focused tests.
3. Re-run:

```bash
cd /home/hermes/.trading
./.venv/bin/python -m pytest tests trading/tests -q
```

4. Re-audit safety-guard references in any touched execution files.
5. Run only read-only/dry-run smoke commands against live paths.
6. Assign the task to the auditor profile for independent intent verification.
7. Dispatch the next task only after the auditor reports each finding `VERIFIED`, or Emmanuel accepts a documented exception.

## 8. Product and strategy decision

The strongest credible positioning is:

> An evidence-based NSE investment-intelligence and paper-portfolio platform built around Kenyan market realities.

Do not claim live intraday data, guaranteed returns, fully autonomous broker execution, or proven alpha.

Keep the strategy frozen until the **2026-11-04** gate except for correctness and safety repairs. Recompute the gate using a synchronized, net-of-cost, eligible-universe benchmark. If the strategy still trails materially, the honest conclusion is that the infrastructure is useful but the strategy has not earned deployment.
