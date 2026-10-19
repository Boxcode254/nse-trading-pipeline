# Proposal: Tiered Sector Caps (review, NOT applied)

## Context
User asked: "should we cap a sector if it's making profit? eg banking."
Answer: cap for RISK (correlation), not to punish winners. Current system
over-trims banking because TWO conflicting caps exist:

- `config.MAX_SECTOR_EXPOSURE_PCT = 25.0`  (auto_trader.py:718 force-sell trigger)
- `target_allocation.SECTOR_CAP_WARN_PCT = 55.0` / `HARD = 60.0`
  (rebalancer's intended banking ceiling ~53.8% = target 48.8 + tol 5)

The 25% wins at execution -> banking force-sold at 25% even though strategy
targets ~49%. Result: constant trim pressure, ABSA UNKNOWN sell loops, book
stuck at 42% (over 25 = always "violating").

## Exact tunables today
- config.py:263  EXECUTION_CONFIG["max_sector_exposure_pct"] = 25.0
- config.py:275  MAX_SECTOR_EXPOSURE_PCT = 25.0  (backward-compat alias)
- target_allocation.py:149-150  WARN 55.0 / HARD 60.0
- target_allocation.py:233  max_sector_exposure_pct() = max(target+tol) ~53.8
- auto_trader.py:718  `if pct > config.MAX_SECTOR_EXPOSURE_PCT:` -> force sell

## Proposal: per-sector tiered caps + momentum gate
Replace the single 25% with a SECTOR_CAPS dict keyed by sector, each with a
WARN and HARD, sized to correlated-downside tolerance (not "is it winning"):

    SECTOR_CAPS = {
        "banking":   {"warn": 40.0, "hard": 45.0},   # core edge, correlated -> bounded but allowed to run
        "telecom":   {"warn": 25.0, "hard": 30.0},
        "energy":    {"warn": 25.0, "hard": 30.0},
        "insurance": {"warn": 20.0, "hard": 25.0},
        "consumer":  {"warn": 20.0, "hard": 25.0},
        "other":     {"warn": 15.0, "hard": 20.0},
    }

Plus a momentum gate on the HARD trim: only force-sell at HARD if the sector's
recent momentum is fading (e.g. sector avg return < 0 over N days); if the
sector is still trending up, raise HARD by +10pts (let winners run) instead of
trimming. This converts "cap because winning" into "cap because risk + momentum
rolling over".

## Effects
- Banking can run to ~40-45% (was force-sold at 25%) -> stops punishing the edge.
- Still bounded: a banking blowout (correlated NPL/CBK shock) trims at 45%.
- ABSA UNKNOWN loop: banking no longer perpetually "over cap" at 25, so the
  forced trim stops firing every day; the pending-reconcile fix still resolves
  the stuck order.

## Risks / caveats
- Raising banking cap increases correlated drawdown risk if banks sell off
  together. 40-45% is the deliberate tolerance; do not exceed without backtest.
- Momentum gate adds a signal; keep it simple (sector avg return vs lookback)
  to avoid the "noisy news overlay" trap we already ruled out for news.
- This is a PAPER book -> safe to experiment, but change ONE thing and measure.

## Not changing
- Per-name stops, cash reserve (20%), single-name cap, macro breaker.
- News remains alerting-only (POLICY.md).

## Next step
If approved: implement SECTOR_CAPS in config.py, wire auto_trader.py:718 and
target_allocation guardrails to read per-sector values, add the momentum gate,
unit-test, then apply. Commit + push separately from the UNKNOWN-order fix.
