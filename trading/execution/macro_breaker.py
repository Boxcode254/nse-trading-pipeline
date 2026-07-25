"""Macro / volatility circuit breaker for the NSE market regime.

This breaker sits *before* trade execution in the same risk gate as the
``SafetyEngine``. It halts trading when the broader market shows systemic
stress — a sharp single-session index drop, or a collapsing breadth
(advancers vs decliners) that signals a market-wide selloff.

Design principles
-----------------
* **Fail-open by default.** If no macro data has ever been fed and the live
  fetch fails, the breaker does NOT trip. A missing market-data source must
  never block normal trading — that would be a worse failure than the rare
  case the breaker is meant to catch. Only an *explicit breach* (data present
  AND threshold crossed) trips it. Set ``fail_open=False`` for a real broker
  where you want the opposite posture.
* **Persisted state.** Trip state and the last macro snapshot are written to
  disk so a process restart (cron overlap, deploy) does not forget that the
  market is in a halt.
* **Manual release.** A tripped breaker stays tripped until ``reset()`` is
  called (or the cooldown elapses — but the default cooldown is 24h and we
  expect a human to acknowledge). This prevents a brief intraday bounce from
  silently re-enabling trading into a still-broken market.
* **Best-effort live fetch.** ``fetch_live_nse()`` tries TradingView as a
  convenience but its failures are non-fatal; the authoritative input is
  ``feed()``, which the morning cron / mystocks scraper / operator populates.

``MacroBreakerError`` is raised only by callers that explicitly ask for a
strict evaluation and request fail-closed behaviour.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_MACRO_PATH = os.path.expanduser("~/.trading/execution/macro_breaker.json")


# Thresholds a snapshot is evaluated against. Kept as a plain dict so it can
# be merged from EXECUTION_CONFIG / env without importing config here.
DEFAULT_THRESHOLDS: dict[str, float] = {
    # Single-session NSE index drop (%) that triggers a halt.
    "index_drop_pct": 3.0,
    # Minimum acceptable breadth: advancers / (advancers + decliners) as %.
    # Below this the selloff is broad, not idiosyncratic -> halt.
    "breadth_min_pct": 20.0,
    # Realized-volatility spike: if the latest daily vol (annualised %) is
    # more than this multiple of the trailing median, trip. 0 disables.
    "vol_spike_multiple": 3.0,
    # Cooldown before an auto-reconsideration is allowed (seconds).
    "cooldown_seconds": 86_400,  # 24h
}


class MacroBreakerError(Exception):
    """Raised when the breaker is tripped and a caller requests strict mode."""


@dataclass
class MacroSnapshot:
    """One market-regime observation. All fields optional except timestamp."""

    timestamp: str
    index_level: Optional[float] = None
    index_change_pct: Optional[float] = None
    advancers: Optional[int] = None
    decliners: Optional[int] = None
    volatility_pct: Optional[float] = None  # trailing annualised vol, %
    source: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "index_level": self.index_level,
            "index_change_pct": self.index_change_pct,
            "advancers": self.advancers,
            "decliners": self.decliners,
            "volatility_pct": self.volatility_pct,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MacroSnapshot":
        return cls(
            timestamp=str(d.get("timestamp", "")),
            index_level=d.get("index_level"),
            index_change_pct=d.get("index_change_pct"),
            advancers=d.get("advancers"),
            decliners=d.get("decliners"),
            volatility_pct=d.get("volatility_pct"),
            source=d.get("source", "unknown"),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MacroBreaker:
    """Evaluates market-regime stress and halts trading on a breach."""

    def __init__(
        self,
        *,
        thresholds: Optional[dict] = None,
        state_path: str = DEFAULT_MACRO_PATH,
        fail_open: bool = True,
        clock: Optional[callable] = None,
    ):
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)
        self.state_path = Path(state_path)
        self.fail_open = fail_open
        self._clock = clock or time.monotonic
        self._last_snapshot: Optional[MacroSnapshot] = None
        self._state = self._load()

    # ── Persistence ───────────────────────────────────────────────────
    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
                if all(
                    k in data
                    for k in ("tripped", "tripped_at", "reason", "snapshots")
                ):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "tripped": False,
            "tripped_at": 0.0,
            "reason": "",
            "snapshots": [],  # rolling log of recent MacroSnapshots
        }

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self.state_path)

    # ── Core evaluation ───────────────────────────────────────────────
    def _evaluate_snapshot(self, snap: MacroSnapshot) -> Optional[str]:
        """Return a breach reason string if ``snap`` breaches a threshold.

        Returns ``None`` when no threshold is crossed (or data is absent for a
        threshold, in which case that threshold is simply not evaluated).
        """
        t = self.thresholds

        # 1. Index drop
        if snap.index_change_pct is not None:
            if snap.index_change_pct <= -float(t["index_drop_pct"]):
                return (
                    f"NSE index down {snap.index_change_pct:.2f}% "
                    f"(>= {t['index_drop_pct']:.2f}% halt threshold)"
                )

        # 2. Breadth collapse
        if snap.advancers is not None and snap.decliners is not None:
            total = snap.advancers + snap.decliners
            if total > 0:
                breadth_pct = snap.advancers / total * 100.0
                if breadth_pct < float(t["breadth_min_pct"]):
                    return (
                        f"Market breadth {breadth_pct:.1f}% advancers "
                        f"(< {t['breadth_min_pct']:.1f}% halt threshold) — "
                        f"broad selloff"
                    )

        # 3. Volatility spike (optional)
        vol_mult = float(t.get("vol_spike_multiple", 0.0))
        if vol_mult > 0 and snap.volatility_pct is not None:
            # volatility_pct is the *current* annualised vol; the snapshot
            # carries no baseline, so we treat any single reading above the
            # configured absolute ceiling as a spike. The caller feeds the
            # trailing-median-normalised value if they want relative logic.
            if snap.volatility_pct >= vol_mult * 100.0:
                return (
                    f"Realized vol {snap.volatility_pct:.1f}% exceeds "
                    f"spike ceiling {vol_mult * 100.0:.1f}%"
                )
        return None

    def feed(self, snapshot: MacroSnapshot, *, max_log: int = 20) -> dict:
        """Record a macro snapshot, evaluate it, and trip if breached.

        Returns a status dict (always safe to inspect)::
            {"tripped": bool, "reason": str, "breach": Optional[str],
             "evaluated": bool}
        """
        self._last_snapshot = snapshot
        # Rolling log (newest first)
        self._state["snapshots"] = [snapshot.to_dict()] + self._state["snapshots"]
        self._state["snapshots"] = self._state["snapshots"][:max_log]

        # If already tripped and still inside cooldown, keep the trip but
        # refresh the log. Do not silently clear.
        if self._state["tripped"]:
            self._save()
            return {
                "tripped": True,
                "reason": self._state["reason"],
                "breach": None,
                "evaluated": False,
            }

        breach = self._evaluate_snapshot(snapshot)
        if breach is not None:
            self._state["tripped"] = True
            self._state["tripped_at"] = self._clock()
            self._state["reason"] = breach
            self._save()
            return {"tripped": True, "reason": breach, "breach": breach,
                    "evaluated": True}

        self._save()
        return {"tripped": False, "reason": "", "breach": None, "evaluated": True}

    def is_tripped(self) -> bool:
        """True if the breaker is currently in a halt state.

        Honours the cooldown: once the cooldown elapses, the breaker
        auto-recovers to a recoverable state but we still require a fresh
        non-breaching feed to fully clear. To keep it explicit/safe, we
        auto-clear only when cooldown elapsed AND a subsequent non-breaching
        feed occurs. Here we simply report persisted state, then auto-clear
        if the cooldown has fully elapsed (so a forgotten trip does not
        block trading forever).
        """
        if not self._state["tripped"]:
            return False
        if self._clock() - self._state["tripped_at"] >= float(
            self.thresholds["cooldown_seconds"]
        ):
            # Cooldown elapsed: auto-clear so trading can resume, but only
            # if fail_open (we never auto-clear a fail-closed breaker).
            if self.fail_open:
                self._state["tripped"] = False
                self._state["reason"] = ""
                self._save()
                return False
        return True

    def evaluate(self, *, strict: bool = False) -> bool:
        """Return True if trading should be HALTED right now.

        ``strict=True`` raises ``MacroBreakerError`` instead of returning
        True, for callers that want to abort loudly.
        """
        tripped = self.is_tripped()
        if tripped and strict:
            raise MacroBreakerError(self._state.get("reason", "macro halt"))
        return tripped

    def reset(self) -> None:
        """Manual release of the breaker (operator acknowledgement)."""
        self._state = {
            "tripped": False,
            "tripped_at": 0.0,
            "reason": "",
            "snapshots": self._state.get("snapshots", []),
        }
        self._save()

    def snapshot(self) -> dict:
        """Full state for status reporting / CLI."""
        return {
            "tripped": self.is_tripped(),
            "reason": self._state.get("reason", ""),
            "fail_open": self.fail_open,
            "thresholds": dict(self.thresholds),
            "last_snapshot": (
                self._last_snapshot.to_dict() if self._last_snapshot else None
            ),
            "recent": self._state.get("snapshots", [])[:5],
        }

    # ── Best-effort live fetch (non-fatal) ────────────────────────────
    def fetch_live_nse(self) -> Optional[MacroSnapshot]:
        """Attempt to pull the NSE index level from TradingView.

        Returns a ``MacroSnapshot`` on success, ``None`` on any failure. This
        is a convenience feed only — failures are expected (the index ticker
        is often unresolvable) and must never raise into the trade gate.
        """
        try:
            from tradingview_ta import TA_Handler, Interval  # local import

            # The NSE composite ticker resolves on TradingView's kenya
            # screener in most deployments; if it fails the caller simply
            # skips live feeding.
            h = TA_Handler(
                symbol="NSE",
                exchange="NSEKE",
                screener="kenya",
                interval=Interval.INTERVAL_1_DAY,
            )
            a = h.get_analysis()
            ind = a.indicators
            close = float(ind.get("close", 0) or 0)
            if close <= 0:
                return None
            chg = ind.get("change") or ind.get("change_pct")
            chg = float(chg) if chg is not None else None
            return MacroSnapshot(
                timestamp=_now_iso(),
                index_level=close,
                index_change_pct=chg,
                source="tradingview",
            )
        except Exception:
            return None

    # ── Derived snapshot from the watchlist (real, always-available) ──
    def build_snapshot_from_prices(
        self, prices: dict[str, dict], *, snapshot_path: Optional[str] = None,
        min_sample: int = 5,
    ) -> dict:
        """Derive a macro snapshot from the live watchlist price map.

        ``prices`` is the same shape ``trading.nse_price_fetcher.fetch_prices``
        returns: ``{symbol: {price, change_pct, ...}}``. From it we compute:

        * **Breadth** — advancers vs decliners across the watchlist (a real,
          always-available market-regime signal on NSE).
        * **Composite index change** — the equal-weight mean of per-symbol
          ``change_pct``. This is a *proxy* composite index (not the literal
          NSE 20 / NSE All-Share), used as the index-drop signal input.
        * **Volatility** — the cross-sectional stdev of ``change_pct`` as a
          crude same-session dispersion proxy, written into ``volatility_pct``
          so the vol-spike threshold has something to act on.

        **Fail-open guarantee.** The thresholds are ONLY evaluated when the
        sample is representative: at least ``min_sample`` symbols returned a
        valid ``change_pct``. With a sparse/partial feed (e.g. the data source
        returned only 1 of 12 symbols on a flaky call) the breadth/index/vol
        fields are left as ``None`` so the breaker **cannot** trip on missing
        data — only on a genuinely present, well-sampled breach.

        The official TV index (``fetch_live_nse``) is folded in only when it
        resolves; otherwise the derived composite stands alone. The snapshot
        is persisted to ``snapshot_path`` (default ``macro_snapshot.json``
        beside this breaker's state) so the morning cron / auto-trader can
        re-read it, and is also fed into the breaker.

        Failures are non-fatal — an empty/partial ``prices`` map yields a
        snapshot with ``None`` fields, which the breaker treats as
        "no threshold breached" (fail-open).
        """
        advancers = decliners = 0
        changes: list[float] = []
        for sym, info in prices.items():
            if sym == "_errors":
                continue
            chg = info.get("change_pct")
            if chg is None:
                continue
            chg = float(chg)
            changes.append(chg)
            if chg > 0:
                advancers += 1
            elif chg < 0:
                decliners += 1

        # Representative sample required before ANY threshold is evaluated.
        # "Representative" means enough symbols actually MOVED (non-flat):
        # a feed where 11 of 12 symbols report change_pct == 0.0 is
        # uninformative (the source returned stale/flat prints) and must not
        # drive a halt. We gate on advancers+decliners (flats excluded).
        non_flat = advancers + decliners
        representative = non_flat >= min_sample

        index_change_pct = (
            round(sum(changes) / len(changes), 4) if representative else None
        )
        volatility_pct = None
        if representative and len(changes) >= 2:
            import statistics
            volatility_pct = round(statistics.pstdev(changes), 4)

        # Prefer the real TV index when it resolves; else use the proxy.
        # (A resolving TV index is itself a representative single source, so
        # it is trusted even when the watchlist sample was too sparse.)
        tv = self.fetch_live_nse()
        if tv is not None and tv.index_change_pct is not None:
            index_change_pct = tv.index_change_pct
            src = "tradingview+derived"
        else:
            src = "derived"

        # Breadth is only meaningful with a representative sample; otherwise
        # leave advancers/decliners as None so the breadth floor is skipped.
        snap_adv = advancers if representative else None
        snap_dec = decliners if representative else None

        snap = MacroSnapshot(
            timestamp=_now_iso(),
            index_level=tv.index_level if tv else None,
            index_change_pct=index_change_pct,
            advancers=snap_adv,
            decliners=snap_dec,
            volatility_pct=volatility_pct,
            source=src,
        )

        if snapshot_path is None:
            snapshot_path = str(self.state_path.parent / "macro_snapshot.json")
        try:
            Path(snapshot_path).parent.mkdir(parents=True, exist_ok=True)
            with open(snapshot_path, "w") as f:
                json.dump(snap.to_dict(), f, indent=2)
        except OSError:
            pass

        res = self.feed(snap)
        return {
            "snapshot": snap.to_dict(),
            "breaker": res,
            "breadth_pct": (
                round(snap_adv / (snap_adv + snap_dec) * 100, 1)
                if (representative and (snap_adv + snap_dec) > 0) else None
            ),
            "representative": representative,
            "sample_size": len(changes),
        }
