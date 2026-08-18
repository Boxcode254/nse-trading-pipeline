# Trading Bot Full Audit — 2026-08-18 (reference for engineer tasks)

Author: default profile (Hermes). Verdict: DO NOT TRADE LIVE. Paper bot is currently non-functional.

Task bodies are self-contained. This file is the deep-dive reference — read it only if a task body references AUDIT-###.

## CRITICAL findings (blocking)

### AUDIT-001 — Safety gate crashes on every live trade attempt
- Location: `trading/execution/safety.py:467` `_is_emergency_stop_active()`; `_default_config()` lines 40-60
- Problem: `_default_config()` returns `dict(EXECUTION_CONFIG)` which LACKS `emergency_stop_path` and `macro_state_path` (only the fallback literal has them). `check_order()` line 105 → `KeyError: 'emergency_stop_path'`.
- Proof: `ExecutionEngine(PaperBroker(...), SafetyEngine(), production=True)` executing `BUY KCB 1 @ 90` → KeyError. Also `test_jul17_freeze_regression_does_not_skip_as_target_met` fails with the same KeyError.
- Fix: merge EXECUTION_CONFIG OVER the fallback literal: `merged = dict(_FALLBACK); merged.update(dict(EXECUTION_CONFIG)); return merged` so the two path keys always survive.
- Test: `SafetyEngine().check_order(OrderRequest(symbol="KCB", side="BUY", quantity=1, price=90.0), {}, AccountInfo(...))` must not raise.

### AUDIT-002 — `_port_state_for_safety` divides per-share avg_cost by shares (gate stop-loss corrupted)
- Location: `trading/auto_trader.py:565-580` (git HEAD line 546)
- Problem: state.json `avg_cost` is PER-SHARE (SCOM=35.5379). Code does `avg = avg_cost / shares` → 0.088. should_stop_loss then sees loss% ~40,000%. Every BUY into a held position is blocked as `stop_loss_blocked`; every SELL mislabeled.
- Proof: live book — KNRE shows +232,395% loss.
- Fix: `avg = float(avg_cost)` (value is already per-share).
- Test: `_port_state_for_safety` on the live book shape → SCOM avg_cost == 35.5379.

### AUDIT-003 — Cash-reserve deadlock: every BUY permanently skipped
- Location: `trading/auto_trader.py:1114-1116`; `config.EXECUTION_CONFIG["cash_reserve_pct"]=20`
- Problem: cash_reserve = total × 20% = KES 20,648 > cash 16,320 → available_cash = 0 → every BUY skipped with "Not enough cash (KES 0 available)". Strategy target is 10% cash (target_allocation.CASH_RESERVE_PCT=10). Book frozen since 2026-08-04.
- Proof: `available_cash = min(max(0, 16320.34 - 20648.25), 51620.63) = 0.00`; plan generates `BUY 19 KCB (KES 1,710)`.
- Fix: align config.CASH_RESERVE_PCT to 10.0 (matching target_allocation); and guard: if cash - reserve <= 0 but cash > MIN_TRADE_KES, allow deploying down to a hard floor (cash × 5%) instead of zero.
- Test: book at 15% cash with 10% reserve → available_cash > 0; book below reserve floor → still > 0.

### AUDIT-004 — Realised P&L excludes fees; PaperBroker equity uses cost basis
- Location: `trading/portfolio/engine.py:541`; `trading/execution/brokers/paper.py:74`
- Problem: `realised = (effective_sell_price - avg_cost) * shares` — fee (1.5%+KES60) never deducted from realised P&L (slippage IS in effective price). PaperBroker.get_account().equity = cash + Σ total_cost (cost basis) → exposure/daily-loss denominators understate true market value.
- Fix: engine.py `realised = (effective_sell_price - existing.avg_cost) * shares_to_sell - fee`. paper.py equity = cash + Σ(shares × live price) via price_source.resolve_prices (fall back to total_cost on error); get_positions market_value likewise.
- Test: round-trip BUY→SELL → realised == gross − 2×(fee+slip); PaperBroker equity > cost basis when prices moved up.

### AUDIT-005 — Backtest metrics fiction (100% win rate / PF 4521)
- Location: `backtest_live_result.json`; `trading/backtest/live_strategy_backtest.py:364-400`
- Problem: win_rate/PF computed from SELL-trades-only with a stub-then-approx; the runnable module + result file still emitted 100% win rate / PF n/a because the replay generates only 5 all-winning SELLs and has NO loader for the live ledger.
- **CORRECTION (auditor, 2026-08-18):** the audit's original "8/22 = 36%" was stale. Current ledger: **16/22 wins (72.7%) by stored realised_pnl; 12/22 (54.5%) by FIFO replay**. The requirement stands: the module must emit ledger-derived honest numbers (or explicitly labeled ledger section), never print 100% when the real ledger contains losses.
- Status: unit metric fix VERIFIED (2W/1L → 66.7%/PF 2.75; all-loss → PF None; no more 4521); rework t_6a1cab25 to wire the live ledger / separate labeled metrics.
- Fix: wire portfolio/transactions.json into run_backtest (--live-ledger flag defaulting on), or emit clearly labeled ledger-derived metrics alongside replay-only numbers.
- Test: `python -m trading.backtest.live_strategy_backtest` must NOT print 100% win rate when the live ledger has losing sells; backtest_live_result.json must carry the ledger-derived win_rate (~54-73%) with correct PF, or an explicit labeled ledger section.

### AUDIT-006 — state.json prices stale; integrity FAIL not surfaced
- Location: `portfolio/state.json` (updated_at 2026-08-05); `scripts/refresh-mtm.py`
- Problem: 9/11 positions' current_value don't match latest AXYS closes; book_integrity_check logs STATUS=FAIL (2026-08-18 19:36) but nothing re-saves state.json with fresh prices (only mtm_state.json).
- Fix: in refresh-mtm.py, after writing mtm_state, also re-save state.json current_value via `price_source.apply_authoritative_prices` (write-side chokepoint), preserving shares/cash/avg_cost.
- Test: after a price-only refresh with no trades, state.json current_value == AXYS × shares for every covered symbol.

## HIGH findings (non-blocking, do not fix in these tasks)

- AUDIT-007: 6/342 tests failing — sector-cap WARN/HARD test (banking 40/45 vs test 55/60) + AXYS direction regression + emergency-stop KeyError test + 2 sandbox path + 1 sector-map. Fix tests to match config.sector_cap; single-source SECTOR_CAP_HARD_PCT usage in target_allocation BUY loops (lines 806/862) to config.sector_cap.
- AUDIT-008: outcome_ingest writes zero pnl_pct / hold_days — `total` never populated in candidates dict (outcome_ingest.py:159-166); derive pnl_pct from realised_pnl / (shares × price).
- AUDIT-009: backtest replay uses same-day close (`_price_on` = `df[df["date"] <= date]`) — look-ahead; use strictly prior close.
- AUDIT-010: dry-run fee math (flat 2% headroom) ≠ live trade_cost (1.5%+floor+slip) — align.
- AUDIT-012: plan prices (TradingView fetch_prices) vs execution prices (mystocks/Mansa/CSV) diverge.
- AUDIT-013: snapshot price_source empty for first 5 points; _maybe_snapshot is dead code; equity curve only advances via manual CLI.
- AUDIT-014: trim sizing `max(1, int(sell_value/price))` rounds down; risk_mul up to 4.0 can oversell.
- AUDIT-015: momentum gate reads data/nse_<SYM>.csv with no staleness guard.
- AUDIT-016: safety.record_trade only records daily_gross_loss on SELL realised<0; daily_realised_pnl never updated.
- AUDIT-017: old backtest/engine.py has zero transaction costs.

## Environment / hygiene

- AUDIT-020: untracked-in-git subsystems: price_source.py, learning_engine.py, outcome_ingest.py, live_strategy_backtest.py, append_equity_snapshot.py, learning_cron.py (all `??`).
- Cron runs on-disk code (git ≠ live). Verify with `py_compile` + full test suite after edits.
- Run tests: `cd ~/.trading && ./.venv/bin/python -m pytest trading/tests/ tests/ -q`
- Do NOT touch live state.json / transactions.json. Work on code + tests only. Dry-run for verification: `./.venv/bin/python -m trading.auto_trader --dry-run`.
