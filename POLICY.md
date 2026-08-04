# Trading System Policy

This file is authoritative. It governs what the auto-trader (and any subagent
or profile that touches `trading/`) may and may not do.

## POLICY 1 — News is CONTEXT/ALERTING ONLY, never an execution input

**Status:** ACTIVE (locked 2026-08-04)
**Owner:** Kratos (Emmanuel)
**Enforced by:** tripwire comments in `trading/auto_trader.py` and
`trading/target_allocation.py`; see grep for "POLICY TRIPWIRE".

### Rule
The auto-trader (`trading.auto_trader.run_auto_trade`) makes decisions from
**ONLY** these inputs:
- portfolio state (`portfolio/state.json`, `mtm_state.json`)
- live prices (Mansa / mystocks cache)
- allocation rules (`target_allocation`, risk weights, sector caps)
- the SafetyEngine (macro breaker, gap filter, cash limits)

The following are **explicitly FORBIDDEN** as execution inputs:
- `news/news_store.json` (Telegram-fed Business Daily stories)
- `market_intel` news cache / scanner output
- any headline sentiment score, LLM news summary, or "impact" tag
- any human-supplied news brief

### Why
The system's edge is that it is *systematic* (rules + prices), not headline-
driven. News is laggy, agenda-laden, and already priced in by the time it is
readable. Wiring sentiment into execution adds noise to the cleanest signal
and produces worse expected value. This was confirmed by tracing the code:
`auto_trader.py` imports no news module; `target_allocation` reads only MTM.

### Allowed use of news
- Human alerting / context (the `news_store.py` `⚡ PORTFOLIO IMPACT` flag).
- A human MAY decide to act on news; the bot does not.
- A *hard* exclude-list for catastrophic, rule-based events (suspension,
  delisting, fraud probe) IS permitted — but only as an explicit symbol list,
  never as a fuzzy sentiment score.

### Forbidden patterns (do not implement)
- `from trading.services.market_intel import ...` inside `auto_trader.py`
- Reading `news_store.json` inside `target_allocation.py` or the safety engine
- Any `sentiment_weight`, `news_score`, `headline_adjust` parameter in rebalance
- A subagent "helpfully" adding news to the decision path

If you are a subagent/employee profile and think news should drive trades,
STOP and ask Kratos. Do not wire it in.
