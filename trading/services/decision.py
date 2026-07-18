"""Decision Engine — holistic portfolio allocation.

The decision engine sits above the ranking engine and below the
paper portfolio. It produces a single strategic allocation that an
investor can use as a north star for the next rebalance, instead of
acting on each per-asset signal in isolation.

Architecture
------------

::

    Market Regime (research/regimes.py)
                |
    Ranking Scores (services/ranking.py)
                |
    Portfolio State (portfolio/engine.py)
                |
                v
       DECISION ENGINE  (this module)
                |
                v
       AllocationProposal  (AllocationLine list + summary + rationale)

Public surface
--------------

* :func:`assess_strategy_tilt` — pure function: regime + scores → tilt
* :func:`target_allocation` — pure function: tilt → category targets
* :func:`distribute_equities` — pure function: tilt + rankings → per-stock
* :func:`generate_proposal` — orchestrator: pulls all inputs, returns proposal
* :func:`format_proposal` — render an :class:`AllocationProposal` for the CLI
* :func:`build_rationale` — plain-English "why" for the proposal

Design rules
------------

* No external I/O. Every function is pure given its inputs. The
  orchestrator (:func:`generate_proposal`) is the only function that
  touches the portfolio engine, ranking service, and regime classifier.
* The proposal is *deterministic*. Given the same regime, the same
  rankings, and the same portfolio state, the output is byte-identical.
  No LLM calls, no randomness.
* Edge cases are handled at the orchestrator level: empty rankings
  fall back to equal-weight equities; missing regime defaults to
  ``Sideways`` (Balanced tilt); missing portfolio returns a
  theoretical ``current_pct=0`` proposal.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .. import config
from . import ranking as ranking_svc


# ── Constants: tilt rules ─────────────────────────────────────────────
# Each tilt is a tuple of (cash_pct, equities_pct, forex_pct, gold_pct,
# single_stock_cap_pct). The four category targets always sum to 100.
# The single-stock cap is applied AFTER distributing equities.

# Defensive: high vol / bear / unknown → defensive
DEFENSIVE_TARGETS: dict[str, float] = {
    "cash":      15.0,
    "equities":  20.0,
    "forex":     10.0,
    "gold":      20.0,
    "tbills":    35.0,
}
DEFENSIVE_SINGLE_CAP = 15.0

# Balanced: sideways / neutral
BALANCED_TARGETS: dict[str, float] = {
    "cash":      10.0,
    "equities":  40.0,
    "forex":     10.0,
    "gold":      15.0,
    "tbills":    25.0,
}
BALANCED_SINGLE_CAP = 20.0

# Growth: bull market
GROWTH_TARGETS: dict[str, float] = {
    "cash":       5.0,
    "equities":  55.0,
    "forex":      5.0,
    "gold":      10.0,
    "tbills":    25.0,
}
GROWTH_SINGLE_CAP = 30.0

TILT_DEFENSIVE = "Defensive"
TILT_BALANCED = "Balanced"
TILT_GROWTH = "Growth"

VALID_TILTS = (TILT_DEFENSIVE, TILT_BALANCED, TILT_GROWTH)


# ── Data classes ──────────────────────────────────────────────────────

@dataclass
class AllocationLine:
    """One row in the allocation table.

    ``target_pct`` and ``current_pct`` are 0-100 floats (not fractions).
    ``action`` is the trade direction implied by the gap between
    current and target (BUY if target > current + threshold,
    SELL if target < current - threshold, HOLD otherwise).
    """
    label: str
    symbol: str
    category: str
    target_pct: float
    current_pct: float
    action: str  # "BUY" | "SELL" | "HOLD" | "n/a"
    reason: str
    why_hold: str        # "Why this allocation is appropriate right now"
    why_increase: str    # "What would trigger raising this allocation"
    why_reduce: str      # "What would trigger cutting back"
    conviction: str      # "strong" | "moderate" | "weak" — how confident in this line

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AllocationProposal:
    """A complete portfolio allocation recommendation.

    ``allocations`` is a list of :class:`AllocationLine`. The ``summary``
    is a per-category roll-up so the CLI can show both the per-line
    table and the category totals without re-aggregating.
    """
    timestamp: str
    market_regime: str
    strategy_tilt: str
    rationale: str
    allocations: list[AllocationLine] = field(default_factory=list)
    summary: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "market_regime": self.market_regime,
            "strategy_tilt": self.strategy_tilt,
            "rationale": self.rationale,
            "allocations": [a.to_dict() for a in self.allocations],
            "summary": dict(self.summary),
            "notes": list(self.notes),
        }


# ── Pure helpers ─────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def assess_strategy_tilt(
    regime: Optional[str],
    avg_score: Optional[float] = None,
) -> str:
    """Decide the strategic tilt (Defensive / Balanced / Growth).

    Rules (deterministic, no LLM):
    * ``Bear`` or ``High Vol`` or unknown → **Defensive**
    * ``Bull`` and ``avg_score >= 65``   → **Growth**
    * ``Bull`` with weaker score         → **Balanced**
    * ``Sideways``                       → **Balanced**
    * ``Low Vol`` alone                  → **Balanced** (still need risk-on signal to be Growth)

    ``avg_score`` is the average ranking score across the equity
    universe; it gates Growth so we don't go risk-on in a weak bull.
    """
    if regime is None:
        return TILT_BALANCED
    normalised = str(regime).strip()
    # Defensive: bear / high vol
    if normalised in ("Bear", "High Vol"):
        return TILT_DEFENSIVE
    # Bull → Growth only if average conviction is high
    if normalised == "Bull":
        if avg_score is not None and avg_score >= 65.0:
            return TILT_GROWTH
        return TILT_BALANCED
    # Sideways / Low Vol / anything else → Balanced
    return TILT_BALANCED


def target_allocation(tilt: str) -> dict[str, float]:
    """Return category-level target percentages for the given tilt."""
    if tilt == TILT_DEFENSIVE:
        return dict(DEFENSIVE_TARGETS)
    if tilt == TILT_GROWTH:
        return dict(GROWTH_TARGETS)
    return dict(BALANCED_TARGETS)


def _single_stock_cap(tilt: str) -> float:
    if tilt == TILT_DEFENSIVE:
        return DEFENSIVE_SINGLE_CAP
    if tilt == TILT_GROWTH:
        return GROWTH_SINGLE_CAP
    return BALANCED_SINGLE_CAP


def distribute_equities(
    tilt: str,
    rankings: list[dict[str, Any]],
    equity_budget_pct: float,
) -> list[dict[str, Any]]:
    """Distribute the equity budget across ranked stocks by score.

    Returns a list of ``{"symbol": str, "pct": float}`` records. Uses
    score-weighted allocation with a single-name cap.

    Algorithm:
    1. Filter to score >= 25 (config.TIER_REDUCE threshold) so we
       don't invest in "Avoid" names.
    2. If nothing qualifies, fall back to equal-weight across the top
       half of the ranked list (or all of it if fewer than 6).
    3. Weight each name by max(score, 1) so a 0 score never starves
       the line entirely.
    4. Apply single-stock cap; redistribute any excess proportionally
       to uncapped names.
    5. If the cap leaves no room for a name, drop it and re-distribute
       among the rest (handles tiny equity budgets gracefully).
    """
    cap = _single_stock_cap(tilt)
    if equity_budget_pct <= 0 or not rankings:
        return []

    # Step 1+2: pick candidates
    candidates = [r for r in rankings if float(r.get("score", 0.0)) >= 25.0]
    if not candidates:
        # Equal-weight fallback: top half, min 1, max 6
        n_take = max(1, min(6, len(rankings) // 2 or len(rankings)))
        candidates = list(rankings[:n_take])

    # Build a score/recommendation lookup for pass-through
    score_map = {r["symbol"]: (r.get("score", 0.0), r.get("recommendation", "Hold"))
                 for r in rankings}
    def _entry(sym: str, pct: float) -> dict[str, Any]:
        score, rec = score_map.get(sym, (0.0, "Hold"))
        return {"symbol": sym, "pct": pct, "score": score, "recommendation": rec}

    # If a single stock, it takes the entire budget (subject to cap)
    if len(candidates) == 1:
        return [_entry(candidates[0]["symbol"], min(equity_budget_pct, cap))]

    # Step 3: weight by score
    weights = [max(float(r.get("score", 0.0)), 1.0) for r in candidates]
    wsum = sum(weights)
    if wsum <= 0:
        return [_entry(r["symbol"], equity_budget_pct / len(candidates)) for r in candidates]

    raw = [equity_budget_pct * w / wsum for w in weights]

    # Step 4: cap each name; redistribute any excess proportionally
    # to uncapped names until either excess is gone or every name is
    # at the cap. We keep the gap bookkeeping simple:
    #   clipped[i] = min(raw[i], cap)
    #   excess[i] = max(0, raw[i] - cap)
    clipped = [min(v, cap) for v in raw]
    excess_per = [max(0.0, v - cap) for v in raw]
    for _ in range(8):  # iterate to settle
        total_excess = sum(excess_per)
        if total_excess <= 0.005:
            break
        uncapped_idx = [i for i, v in enumerate(clipped) if v < cap - 1e-6]
        if not uncapped_idx:
            break
        uc_w = sum(weights[i] for i in uncapped_idx)
        if uc_w <= 0:
            break
        for i in uncapped_idx:
            add = total_excess * weights[i] / uc_w
            clipped[i] += add
            # Don't recurse: if a name crosses the cap, we still
            # account for the excess generated next pass.
            if clipped[i] > cap:
                excess_per[i] += clipped[i] - cap
                clipped[i] = cap
            else:
                excess_per[i] = 0.0

    # Step 5: drop zero-budget lines. If a single cap-clipped name
    # pulled the total below the budget, return what we have — the
    # intent ("cap the heavy hitter") is preserved, and the summary
    # roll-up will be honest about the residual.
    nonzero = [(r["symbol"], v) for r, v in zip(candidates, clipped) if v > 0.05]
    if not nonzero:
        # If all are sub-0.05%, return the top-scoring one at min(budget, cap)
        top = max(candidates, key=lambda r: r.get("score", 0.0))
        return [_entry(top["symbol"], min(equity_budget_pct, cap))]
    return [_entry(sym, pct) for sym, pct in nonzero]


# ── Orchestrator ─────────────────────────────────────────────────────

def _detect_regime() -> tuple[str, dict[str, Any]]:
    """Return (regime_label, breakdown_dict).

    Uses the largest equity universe (ranked) to classify the trend
    regime. Falls back to Sideways on any failure.
    """
    try:
        from ..research import regimes as rg
        from . import market
        frames = market.fetch_all()
        if not frames:
            return "Sideways", {}
        # Pick the longest frame for the regime signal
        best_sym, best_df = max(
            frames.items(), key=lambda kv: len(kv[1])
        )
        close = best_df["close"].astype(float)
        if len(close) < 260:
            return "Sideways", {"reason": "insufficient history",
                                "symbol": best_sym}
        trend_reg, vol_reg = rg.classify_regimes(close)
        # Take the latest non-NaN label
        last_trend = trend_reg.dropna().iloc[-1] if not trend_reg.dropna().empty else "Sideways"
        last_vol = vol_reg.dropna().iloc[-1] if not vol_reg.dropna().empty else "Low Vol"
        return str(last_trend), {
            "symbol": best_sym,
            "trend": str(last_trend),
            "volatility": str(last_vol),
        }
    except Exception as exc:  # noqa: BLE001
        return "Sideways", {"reason": f"regime detection failed: {exc}"}


def _load_portfolio_state() -> Optional[dict[str, Any]]:
    """Best-effort load of the paper portfolio. Returns None if not initialised."""
    try:
        from ..portfolio import engine as pf
        if not pf.portfolio_exists():
            return None
        state = pf.load_state()
        symbols = [p.symbol for p in state.positions]
        prices = pf.fetch_latest_prices(symbols) if symbols else {}
        holdings, rows = pf.compute_holdings_value(state, prices)
        total_value = round(state.cash + holdings, 2)
        return {
            "initialised": True,
            "cash": state.cash,
            "holdings_value": holdings,
            "total_value": total_value,
            "positions": rows,
        }
    except Exception:  # noqa: BLE001
        return None


def _current_pct_by_symbol(portfolio: Optional[dict[str, Any]]) -> dict[str, float]:
    """Return {symbol: pct_of_total_value} from the portfolio.

    If no portfolio is initialised, returns an empty dict. The orchestrator
    will then show current=0% for every asset.
    """
    out: dict[str, float] = {}
    if not portfolio:
        return out
    total = portfolio.get("total_value", 0.0) or 0.0
    if total <= 0:
        return out
    out["__cash__"] = round(portfolio["cash"] / total * 100.0, 2)
    for pos in portfolio.get("positions", []):
        sym = pos.get("symbol")
        if not sym:
            continue
        # rows are PortfolioRow dicts from engine.compute_holdings_value
        value = pos.get("value")
        if value is None:
            # Fall back to market_value → value alias used elsewhere
            value = pos.get("market_value")
        if value is None:
            continue
        out[sym] = round(float(value) / total * 100.0, 2)
    return out


def _derive_action(target: float, current: float, threshold: float = 3.0) -> str:
    """Map (target, current) to BUY / SELL / HOLD.

    Threshold prevents the proposal from shouting BUY on a 0.1% gap.
    """
    if current is None:
        return "BUY" if target > 0 else "HOLD"
    delta = target - current
    if delta > threshold:
        return "BUY"
    if delta < -threshold:
        return "SELL"
    return "HOLD"


def _safe_round(x: float, n: int = 2) -> float:
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return 0.0


def compute_category_targets(
    tilt: str,
    avg_score: float,
    regime: str,
    volatility: str,
) -> dict[str, float]:
    """Compute 5-category allocation targets with dynamic band adjustment.

    Takes the base tilt targets and adjusts within sensible bands based on
    market conditions. All 5 categories always sum to 100.
    T-Bills always have a floor of 20% (income floor).
    """
    base = {
        TILT_DEFENSIVE: DEFENSIVE_TARGETS,
        TILT_BALANCED: BALANCED_TARGETS,
        TILT_GROWTH: GROWTH_TARGETS,
    }.get(tilt, BALANCED_TARGETS)

    # Bands: (min, max) per category
    bands = {
        "cash":      (5.0, 20.0),
        "equities":  (15.0, 60.0),
        "forex":     (5.0, 15.0),
        "gold":      (5.0, 25.0),
        "tbills":    (20.0, 40.0),
    }

    targets = dict(base)

    # Adjustment: higher avg_score = more equities, less tbills
    score_factor = (avg_score - 50.0) / 50.0  # -1.0 to +1.0
    eq_adjust = score_factor * 10.0
    tb_adjust = -score_factor * 5.0

    # Regime adjustment
    if regime and "bear" in regime.lower():
        eq_adjust -= 10.0
        tb_adjust += 10.0
    elif regime and "bull" in regime.lower():
        eq_adjust += 5.0
        tb_adjust -= 5.0

    # Volatility adjustment
    if volatility and "high" in volatility.lower():
        eq_adjust -= 5.0
        tb_adjust += 5.0

    # Apply adjustments
    targets["equities"] = _clamp(targets["equities"] + eq_adjust, bands["equities"])
    targets["tbills"] = _clamp(targets["tbills"] + tb_adjust, bands["tbills"])

    # Ensure T-Bills floor
    targets["tbills"] = max(targets["tbills"], 20.0)

    # Normalize to sum to 100
    total = sum(targets.values())
    if total != 100.0:
        scale = 100.0 / total
        for k in targets:
            targets[k] = round(targets[k] * scale, 1)
        # Fix rounding drift on the largest category
        diff = 100.0 - sum(targets.values())
        if diff:
            targets[max(targets, key=targets.get)] += diff

    return targets


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    """Clamp value to [min, max]."""
    return max(bounds[0], min(bounds[1], value))


def generate_proposal(
    portfolio: Optional[dict[str, Any]] = None,
    rankings: Optional[list[dict[str, Any]]] = None,
    regime: Optional[str] = None,
    regime_meta: Optional[dict[str, Any]] = None,
    tilt: Optional[str] = None,
) -> AllocationProposal:
    """Build a complete :class:`AllocationProposal`.

    All inputs are optional and default to "fetch what you can":
    * ``portfolio``  — None → no portfolio state, theoretical allocation
    * ``rankings``   — None → call :func:`ranking_svc.build`
    * ``regime``     — None → auto-detect from market data
    * ``tilt``       — None → auto-pick from regime + score average
    """
    notes: list[str] = []

    # 1. Regime
    if regime is None:
        regime, regime_meta = _detect_regime()
    regime_meta = dict(regime_meta or {})

    # 2. Rankings
    if rankings is None:
        try:
            result = ranking_svc.build()
            rankings = result.get("ranked", [])
        except Exception as exc:  # noqa: BLE001
            notes.append(f"ranking fetch failed: {exc}")
            rankings = []

    # 3. Average score for tilt decision (equity universe only)
    equity_ranking = [
        r for r in rankings
        if config.get_asset_category(r["symbol"])["category"] == "equities"
    ]
    avg_score = (
        sum(float(r.get("score", 0.0)) for r in equity_ranking)
        / len(equity_ranking)
        if equity_ranking else None
    )

    # 4. Tilt
    if tilt is None:
        tilt = assess_strategy_tilt(regime, avg_score=avg_score)
    if tilt not in VALID_TILTS:
        notes.append(f"unknown tilt '{tilt}' → Balanced")
        tilt = TILT_BALANCED

    # 5. Category targets
    cat_targets = compute_category_targets(tilt, avg_score or 50.0, regime or "Sideways", "High Vol" if "High Vol" in str(regime_meta.get("volatility")) else "Low Vol")
    equity_budget = cat_targets["equities"]

    # 6. Distribute equities
    stock_alloc = distribute_equities(tilt, equity_ranking, equity_budget)

    # 7. Distribute forex equally between the two monitored pairs
    forex_pairs = config.get_forex_symbols()
    forex_each = (cat_targets["forex"] / len(forex_pairs)) if forex_pairs else 0.0

    # 8. Current allocation from the portfolio
    current_pcts = _current_pct_by_symbol(portfolio)
    notes.extend(_portfolio_notes(portfolio))

    # 9. Build the per-asset lines
    lines: list[AllocationLine] = []

    # Equities
    for entry in stock_alloc:
        sym = entry["symbol"]
        target = _safe_round(entry["pct"])
        meta = config.get_asset_category(sym)
        current = current_pcts.get(sym, 0.0)
        lines.append(AllocationLine(
            label=meta["display"],
            symbol=sym,
            category="equities",
            target_pct=target,
            current_pct=current,
            action=_derive_action(target, current),
            reason=_equity_reason(sym, target, tilt, equity_ranking),
            why_hold=f"Score {entry.get('score', 0)}/100, recommendation {entry.get('recommendation', 'Hold')}",
            why_increase="Higher conviction score or regime shift to Growth",
            why_reduce="Score dropping below 50, or regime shifts defensive",
            conviction="strong" if entry.get('score', 0) >= 75 else "moderate",
        ))

    # Forex — DEFERRED: no broker integration for forex pairs yet.
    # When forex execution is available, uncomment below and adjust cash target.
    # for pair in forex_pairs:
    #     meta = config.get_asset_category(pair)
    #     current = current_pcts.get(pair, 0.0)
    #     lines.append(AllocationLine(
    #         label=meta["display"],
    #         symbol=pair,
    #         category="forex",
    #         target_pct=_safe_round(forex_each),
    #         current_pct=current,
    #         action=_derive_action(forex_each, current),
    #         reason=f"Forex diversification: {meta['sector']} exposure",
    #         why_hold="Diversification into major pairs; regime supports",
    #         why_increase="USD weakness trend / KES volatility",
    #         why_reduce="Regime-driven cash need / reduced risk budget",
    #         conviction="moderate",
    #     ))

    # Gold — DEFERRED: no broker integration for commodities.
    # gold_target = _safe_round(cat_targets["gold"])
    # gold_current = current_pcts.get("__gold__", 0.0)
    # lines.append(AllocationLine(
    #     label="Gold",
    #     symbol="__gold__",
    #     category="commodity",
    #     target_pct=gold_target,
    #     current_pct=gold_current,
    #     action=_derive_action(gold_target, gold_current),
    #     reason="Portfolio hedge — non-correlated asset",
    #     why_hold="Acts as portfolio hedge; non-correlated asset",
    #     why_increase="Bear regime / inflation signal",
    #     why_reduce="Bull/risk-on regime where capital should be productive",
    #     conviction="strong",
    # ))

    # T-Bills — DEFERRED: no broker integration for fixed income.
    # tbills_target = _safe_round(cat_targets["tbills"])
    # tbills_current = current_pcts.get("__tbills__", 0.0)
    # lines.append(AllocationLine(
    #     label="T-Bills",
    #     symbol="__tbills__",
    #     category="fixed_income",
    #     target_pct=tbills_target,
    #     current_pct=tbills_current,
    #     action=_derive_action(tbills_target, tbills_current),
    #     reason="Yield-bearing safety; income floor",
    #     why_hold="Provides yield while preserving capital for deployment",
    #     why_increase="Risk-off regime / high volatility / no equity conviction",
    #     why_reduce="Risk-on regime / better deployment opportunities found",
    #     conviction="strong",
    # ))

    # Cash buffer — uses ACTUAL portfolio cash, not theoretical target.
    # When forex/gold/tbills are deferred, excess cash is intentional.
    cash_current = current_pcts.get("__cash__", 100.0)
    cash_target = max(cat_targets["cash"], cash_current)
    lines.append(AllocationLine(
        label="Cash (KES)",
        symbol="__cash__",
        category="cash",
        target_pct=cash_target,
        current_pct=cash_current,
        action=_derive_action(cash_target, cash_current, threshold=2.0),
        reason=_cash_reason(tilt, cash_current, cash_target),
        why_hold="Operational liquidity buffer for deployment",
        why_increase="Market uncertainty / no attractive opportunities",
        why_reduce="Deployment opportunities found / high conviction signals",
        conviction="strong",
    ))

    # 10. Summary roll-up
    summary = {
        "cash":      _safe_round(sum(l.target_pct for l in lines
                                      if l.category == "cash")),
        "equities":  _safe_round(sum(l.target_pct for l in lines
                                      if l.category == "equities")),
        "forex":     _safe_round(sum(l.target_pct for l in lines
                                      if l.category == "forex")),
        "commodity": _safe_round(sum(l.target_pct for l in lines
                                      if l.category == "commodity")),
        "fixed_income": _safe_round(sum(l.target_pct for l in lines
                                        if l.category == "fixed_income")),
    }

    # 11. Rationale
    rationale = build_rationale(
        regime=regime, regime_meta=regime_meta, tilt=tilt,
        cat_targets=cat_targets, equity_lines=[l for l in lines
                                               if l.category == "equities"],
        avg_score=avg_score, portfolio=portfolio,
    )

    return AllocationProposal(
        timestamp=_now_iso(),
        market_regime=regime,
        strategy_tilt=tilt,
        rationale=rationale,
        allocations=lines,
        summary=summary,
        notes=notes,
    )


# ── Reason builders ──────────────────────────────────────────────────

def _portfolio_notes(portfolio: Optional[dict[str, Any]]) -> list[str]:
    if portfolio is None:
        return ["No portfolio initialised — showing theoretical allocation."]
    if not portfolio.get("positions"):
        return ["Portfolio is 100% cash — strategy is fully uninvested."]
    return []


def _equity_reason(symbol: str, target: float, tilt: str,
                   rankings: list[dict[str, Any]]) -> str:
    rec = next(
        (r for r in rankings if r.get("symbol") == symbol),
        None,
    )
    if rec is None:
        return f"{tilt} tilt: target {target:.0f}%."
    score = float(rec.get("score", 0.0))
    return f"Score {score:.0f}/100 ({rec.get('recommendation', '?')}); {tilt} tilt"


def _gold_reason(tilt: str) -> str:
    if tilt == TILT_DEFENSIVE:
        return "Defensive hedge — overweight during risk-off regimes"
    if tilt == TILT_GROWTH:
        return "Slim hedge — small ballast in a risk-on book"
    return "Portfolio hedge — recommended even though not tracked"


def _cash_reason(tilt: str, current: float, target: float) -> str:
    if current >= 99.0:
        return "Fully uninvested — strategy is the first deployment path"
    if current > target + 5:
        return f"Cash overweight ({current:.0f}%); trim toward {target:.0f}%"
    if current < target - 5:
        return f"Cash underweight ({current:.0f}%); top up toward {target:.0f}%"
    return f"Cash buffer near target ({current:.0f}% vs {target:.0f}%)"


# ── Rationale (plain English) ────────────────────────────────────────

def build_rationale(
    *,
    regime: str,
    regime_meta: dict[str, Any],
    tilt: str,
    cat_targets: dict[str, float],
    equity_lines: list[AllocationLine],
    avg_score: Optional[float],
    portfolio: Optional[dict[str, Any]] = None,
) -> str:
    """Build a 3-5 sentence plain-English explanation of the proposal."""
    parts: list[str] = []

    # 1. Regime + tilt
    regime_label = regime or "Sideways"
    parts.append(
        f"Market regime is {regime_label}."
    )
    vol_tag = regime_meta.get("volatility") if regime_meta else None
    if vol_tag and vol_tag != "Low Vol":
        parts.append(f"Volatility is {vol_tag}.")
    parts.append(f"Strategy tilt: {tilt}.")

    # 2. Category targets in one line
    cat_str = ", ".join(
        f"{int(cat_targets[k])}% {k}" for k in ("equities", "forex", "gold", "tbills", "cash")
    )
    parts.append(f"Target allocation: {cat_str}.")

    # 3. Equity logic
    if equity_lines:
        n = len(equity_lines)
        if avg_score is not None:
            parts.append(
                f"Within equities (avg score {avg_score:.0f}/100), "
                f"weighted by score across {n} name(s) with a "
                f"{int(_single_stock_cap(tilt))}% single-stock cap."
            )
        else:
            parts.append(
                f"Within equities, weighted by score across {n} name(s) "
                f"with a {int(_single_stock_cap(tilt))}% single-stock cap."
            )

    # 4. Gold
    parts.append(
        "Gold acts as portfolio hedge — recommended even though not tracked."
    )

    # 4b. T-Bills
    tbills_pct = int(cat_targets.get("tbills", 0))
    parts.append(
        f"T-Bills at {tbills_pct}% provide a yield-bearing income floor."
    )

    # 5. Portfolio state hint
    if portfolio is None:
        parts.append("No paper portfolio yet — proposal is theoretical.")
    elif not portfolio.get("positions"):
        parts.append("Portfolio is 100% cash — this is the deployment path.")

    return " ".join(parts)


# ── Formatter (for the CLI) ──────────────────────────────────────────

DISPLAY_CATEGORIES: dict[str, str] = {
    "equities": "Equities",
    "forex": "Forex",
    "commodity": "Commodity",
    "cash": "Cash",
    "fixed_income": "Fixed Income",
}

def format_proposal(proposal: AllocationProposal, verbose: bool = False) -> str:
    """Render an :class:`AllocationProposal` as a Rich-formatted string.

    Pure formatting — no I/O. Used by the CLI command; safe to call
    from tests for output comparison.
    """
    from rich.console import Console
    from rich.table import Table

    buf_console = Console(file=None, record=True, width=110)
    # Use a real buffer-backed console so we can capture the string
    import io
    out = io.StringIO()
    console = Console(file=out, force_terminal=False, width=110)

    console.print("[bold]🧠 PORTFOLIO DECISION ENGINE[/]")
    console.print("━" * 50)
    console.print(f"  [bold]Market Regime:[/]  {proposal.market_regime}")
    console.print(f"  [bold]Strategy Tilt:[/]  {proposal.strategy_tilt}")
    console.print(f"  [bold]Generated:[/]      {proposal.timestamp}")
    console.print("")

    # Per-asset table
    console.print("[bold]RECOMMENDED ALLOCATION[/]")
    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("Asset", style="cyan")
    table.add_column("Category")
    table.add_column("Target", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Action", justify="center")
    if verbose:
        table.add_column("Reason")

    # Render in display order (cash + gold last as strategic rails)
    ordered: list[AllocationLine] = []
    by_sym = {l.symbol: l for l in proposal.allocations}
    for sym in config.ASSET_DISPLAY_ORDER:
        if sym in by_sym:
            ordered.append(by_sym[sym])
    # Append any leftover (unknown symbols) at the end
    for line in proposal.allocations:
        if line.symbol not in {l.symbol for l in ordered}:
            ordered.append(line)

    for line in ordered:
        action_emoji = {
            "BUY":  "▲ BUY",
            "SELL": "▼ SELL",
            "HOLD": "─ HOLD",
            "n/a":  "── n/a",
        }.get(line.action, line.action)
        style = {
            "BUY": "green", "SELL": "red", "HOLD": "yellow", "n/a": "dim",
        }.get(line.action, "")
        row = [
            line.label,
            line.category,
            f"{line.target_pct:5.1f}%",
            f"{line.current_pct:5.1f}%",
            f"[{style}]{action_emoji}[/]",
        ]
        if verbose:
            row.append(line.reason)
        table.add_row(*row)
    console.print(table)

    # Category totals
    console.print("")
    console.print("[bold]CATEGORY TOTALS[/]")
    total_table = Table(show_header=True, header_style="bold", expand=True)
    total_table.add_column("Category")
    total_table.add_column("Target", justify="right")
    summary = proposal.summary or {}
    for cat in ("equities", "forex", "commodity", "cash", "fixed_income"):
        total_table.add_row(
            DISPLAY_CATEGORIES.get(cat, cat.title()),
            f"{summary.get(cat, 0.0):5.1f}%",
        )
    console.print(total_table)

    # Rationale
    console.print("")
    console.print("[bold]RATIONALE[/]")
    console.print(proposal.rationale)

    # Notes (non-blocking warnings: regime detection failure, etc.)
    if proposal.notes:
        console.print("")
        console.print("[bold yellow]NOTES[/]")
        for note in proposal.notes:
            console.print(f"  • {note}")

    return out.getvalue()


# ── Convenience: the "decision" entrypoint used by the CLI ───────────

def build(
    portfolio: Optional[dict[str, Any]] = None,
    rankings: Optional[list[dict[str, Any]]] = None,
    regime: Optional[str] = None,
    tilt: Optional[str] = None,
    portfolio_aware: bool = True,
) -> AllocationProposal:
    """Top-level entry point for the CLI.

    * If ``portfolio_aware`` is True (default) and a paper portfolio
      exists, its holdings are merged into the proposal's
      ``current_pct`` values. Otherwise every line shows 0% current.
    * All other args are passed through to :func:`generate_proposal`.
    """
    pf: Optional[dict[str, Any]] = None
    if portfolio_aware:
        if portfolio is not None:
            pf = portfolio
        else:
            pf = _load_portfolio_state()
    return generate_proposal(
        portfolio=pf, rankings=rankings, regime=regime, tilt=tilt,
    )
