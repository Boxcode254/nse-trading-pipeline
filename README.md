# NSE Paper Trading Pipeline

Private source backup for Emmanuel/Kratos’s Nairobi Securities Exchange (NSE) **paper** trading system.

> **Runtime lives on the VPS at `~/.trading`.** Cron jobs execute this tree directly.  
> **Portfolio state is never in git** (`portfolio/*.json` is gitignored).

## What this repo is

| Included | Excluded |
|---|---|
| `trading/` package (auto_trader, portfolio engine, target allocation, CLI, tests) | `portfolio/state.json`, transactions, snapshots |
| Config, strategies, indicators, execution layer | `.env`, API keys, Mansa secrets |
| Tests | `.venv/`, caches, logs, CSV dumps, graphify-out |
| Archive stubs / docs | Live safety state, DBs |

## Layout (high level)

```
trading/
  auto_trader.py          # 10:30 EAT paper execution (cron)
  target_allocation.py  # sector targets (90% invested + 10% cash)
  portfolio/engine.py   # ledger: buy/sell, fees, snapshots
  execution/            # SafetyEngine + PaperBroker
  cli/                  # Typer CLI
  tests/
portfolio/              # runtime only (gitignored state files)
```

## Schedule (EAT, weekdays)

| Time | Job |
|---|---|
| 09:00 | Morning brief / mystocks cache |
| 09–11 */30 | Gap scanner |
| **10:30** | **Auto-trader paper execution** |
| 11:00 | Learning + source independence check |
| 15:30 | Market-close MTM |

## Data assumptions

- Strategy signals: TradingView **daily** bars (EOD), not true intraday.
- Execution prices: mystocks cache → Mansa → CSV.
- Paper fee: **1.0% one-way** proxy for NSE all-in costs.
- Broker for eventual live: AIB-AXYS DigiTrader (manual until API confirmed).

## Local setup

```bash
cd ~/.trading   # or clone into a workdir
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # if present; else pandas + pytest + project deps
.venv/bin/python -m pytest tests/ trading/tests/ -q
```

Secrets: set `MANSA_API_KEY` in the environment (on the VPS: `~/.hermes/.env`). Never commit keys.

## Safety rules for agents / deploys

1. Do **not** commit `portfolio/` state or any `*API_KEY*`.
2. Schema changes to live `state.json` require isolated dry-run + human go-ahead.
3. Cron runs code on disk at `~/.trading` — a bad push does not auto-deploy unless you pull here.
4. After cloning elsewhere, do not point production cron at a half-synced tree without verifying tests.

## License

Private. All rights reserved.
