# Stage-1 Gate — Pre-Registered Decision Rule

**Registered:** 2026-09-15 (EAT) — BEFORE any eqw-v2 benchmark data exists
**Approved by:** Emmanuel (Kratos), directive: "proceed with the recommendations systematically, no mistakes"
**Gate date:** 2026-11-04
**Scope:** the GO/NO-GO decision for deploying the current paper strategy toward live sizing.

## Clarification (dated 2026-09-16, within the amendment window)

Clause bases, as registered on 2026-09-15 and implemented in
`trading/services/benchmark_compare.py::stage1_rule`:

- Clause (a) "strategy net return ≥ 0" and the strategy side of clause (b) use the
  **book-life return**: total portfolio value / 100,000 − 1. The paper book's whole
  life counts, including the pre-window period.
- The benchmark side of clause (b) uses the **window return** from its init base
  (init date 2026-09-15, entry-cost-adjusted base 98,350.00).
- The two bases differ by design and were part of this registration as written on
  2026-09-15. This note exists so the gate verdict cannot be disputed on basis
  ambiguity later. No threshold, date, or clause logic is changed by this note.

## Anti-goalpost clause

This rule is fixed as of registration. Any modification after benchmark data
accumulation begins (first session on/after 2026-09-16) requires:

1. written approval from Kratos dated on or before 2026-10-01, and
2. a full window reset (new benchmark init date, minimum window re-accumulated).

After 2026-10-01 the rule cannot change, and the gate is evaluated as written —
including an INSUFFICIENT_WINDOW outcome.

## Definitions

- **Strategy net return** — `(total portfolio value at valuation / 100,000 − 1) × 100`,
  from the canonical read model (`trading/services/portfolio_status.py`). The paper
  book already applies the trade-cost model (1.5%/side + KES 60 floor + 0.15%
  slippage), so this figure is net of all modeled costs. Includes the strategy's
  cash policy (cash drag is part of what is judged).
- **Benchmark eqw-v2-held** — equal-weight buy-and-hold of the positions held on the
  benchmark init date (init prices = official AXYS closes for that date; feed/CSV
  fallback per the standard authority chain). Value at t =
  `initial_capital × mean(price_t / price_init)` MINUS one-time entry cost computed
  with the same `config.trade_cost` model on the initial deployment.
- **Benchmark eqw-v2-mandate** — same construction over the tradable members of
  `STRATEGY.stocks` (BRIT included only if price data is obtainable at init; any
  exclusion is recorded inside the versioned benchmark record, never silently).
- **Comparable window** — trading sessions from the benchmark init date through
  2026-11-04 inclusive where BOTH the strategy book and the benchmark are valued
  same-date through the same price resolver (the TP-005 machinery).
- **Max drawdown** — peak-to-trough of the strategy equity over the comparable window,
  from the canonical snapshot series.
- **Minimum window** — 20 comparable sessions. Below that, no verdict is possible.

## The rule (evaluated 2026-11-04)

1. **INSUFFICIENT_WINDOW** — if the comparable window has fewer than 20 sessions,
   the gate verdict is INSUFFICIENT_WINDOW and is deferred exactly once to
   2026-12-01 under this same rule. A second insufficiency is a NO-GO.
2. **GO** — all three hold at window end:
   a. strategy net return ≥ 0 (absolute floor: no deployment of a losing system);
   b. strategy net return ≥ eqw-v2-held net return (primary test: value-add over
      passively holding the same picks);
   c. strategy max drawdown < 15% over the window (matches `max_drawdown_halt_pct`).
3. **NO-GO** — any of (a)–(c) fails at window end.

The eqw-v2-mandate comparison is REPORTED alongside the verdict as context
(selection vs opportunity set) but does not gate.

## Verdict provenance requirements

The 2026-11-04 verdict must be produced by `benchmark_compare.compare()` (or its
v2-aware successor) and must print: window session count, strategy net return,
both benchmark net returns, max drawdown, and each rule clause with PASS/FAIL.
A verdict that cannot show its inputs is invalid.
