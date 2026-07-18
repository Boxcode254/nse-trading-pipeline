"""Rule Updater - adjusts signal weights based on outcomes, versions rules."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add trading to path
sys.path.insert(0, str(Path.home() / ".trading"))

from trading.learning.db import get_connection
from trading.signal_profiler import SignalProfiler, create_profiler, SignalMetrics


# Setup logging
LOG_DIR = Path.home() / ".trading" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "rule_updater.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class RuleChange:
    """Proposed change to a signal weight."""
    signal_source: str
    current_weight: float
    proposed_weight: float
    change_pct: float
    rationale: str
    metrics: dict


@dataclass
class RuleUpdateResult:
    """Result of applying rule updates."""
    version: int
    changes: list[RuleChange]
    applied: bool
    report: str
    errors: list[str]


class RuleUpdater:
    """Updates signal weights based on performance metrics."""

    # Thresholds for weight adjustment
    HIGH_CONSISTENCY_THRESHOLD = 20.0
    LOW_CONSISTENCY_THRESHOLD = 5.0
    MAX_WEIGHT = 2.0
    MIN_WEIGHT = 0.0
    WEIGHT_STEP = 0.25  # How much to adjust per step

    def __init__(
        self,
        db_path: Optional[Path] = None,
        min_signals_for_weight: int = 5,
    ):
        self.db_path = db_path
        self.profiler = create_profiler(db_path=db_path)
        self.min_signals = min_signals_for_weight

    def get_current_weights(self) -> dict[str, float]:
        """Get current signal weights from latest rule_version."""
        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT params_json FROM rule_versions
                ORDER BY version DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    params = json.loads(row[0])
                    return params.get("signal_weights", {})
                except (json.JSONDecodeError, KeyError):
                    pass

        # Default equal weights
        return {}

    def get_latest_version(self) -> int:
        """Get latest rule version number."""
        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(version) FROM rule_versions")
            row = cursor.fetchone()
            return (row[0] or 0) + 1

    def propose_changes(self, lookback_days: int = 30) -> list[RuleChange]:
        """Propose weight changes based on signal metrics."""
        metrics_list = self.profiler.get_all_signal_metrics(lookback_days)
        current_weights = self.get_current_weights()

        # Default weight if not set
        default_weight = 1.0

        changes = []
        for m in metrics_list:
            source = m.signal_source
            current_weight = current_weights.get(source, default_weight)

            # Skip if insufficient data
            if m.executed_signals < self.min_signals:
                changes.append(RuleChange(
                    signal_source=source,
                    current_weight=current_weight,
                    proposed_weight=current_weight,
                    change_pct=0.0,
                    rationale=f"Insufficient data ({m.executed_signals} signals, need {self.min_signals})",
                    metrics={"consistency_score": m.consistency_score, "win_rate": m.win_rate,
                            "max_dd": m.max_drawdown_pct, "n": m.executed_signals}
                ))
                continue

            # Get copyability filter result
            copyable = self.profiler.filter_copyable_signals(
                min_win_rate=50.0, max_drawdown=20.0, min_signals=self.min_signals,
                lookback_days=lookback_days
            )
            is_copyable = any(c.signal_source == source for c in copyable)

            proposed_weight = current_weight
            rationale_parts = []

            # High consistency + copyable -> increase
            if m.consistency_score >= self.HIGH_CONSISTENCY_THRESHOLD and is_copyable:
                proposed_weight = min(current_weight + self.WEIGHT_STEP, self.MAX_WEIGHT)
                rationale_parts.append(f"High consistency ({m.consistency_score:.1f}) + copyable")

            # Low consistency or not copyable -> decrease
            elif m.consistency_score <= self.LOW_CONSISTENCY_THRESHOLD or not is_copyable:
                if m.executed_signals >= self.min_signals:
                    proposed_weight = max(current_weight - self.WEIGHT_STEP, self.MIN_WEIGHT)
                    if not is_copyable:
                        rationale_parts.append("Not copyable (low win rate or high drawdown)")
                    else:
                        rationale_parts.append(f"Low consistency ({m.consistency_score:.1f})")

            # On avoid list -> set to 0
            rankings = self.profiler.get_signal_rankings(lookback_days)
            if source in rankings.get("avoid_list", []):
                proposed_weight = self.MIN_WEIGHT
                rationale_parts.append("On avoid list")

            change_pct = ((proposed_weight - current_weight) / current_weight * 100) if current_weight > 0 else 0

            if proposed_weight != current_weight:
                changes.append(RuleChange(
                    signal_source=source,
                    current_weight=current_weight,
                    proposed_weight=round(proposed_weight, 2),
                    change_pct=round(change_pct, 1),
                    rationale="; ".join(rationale_parts) or "No change needed",
                    metrics={
                        "consistency_score": m.consistency_score,
                        "win_rate": m.win_rate,
                        "max_drawdown_pct": m.max_drawdown_pct,
                        "executed_signals": m.executed_signals,
                        "avg_pnl_pct": m.avg_pnl_pct,
                        "sharpe_like": m.sharpe_like,
                    }
                ))

        return changes

    def apply_changes(self, changes: list[RuleChange], dry_run: bool = True) -> RuleUpdateResult:
        """Apply proposed changes (or dry run)."""
        errors = []
        new_version = self.get_latest_version()

        # Build new weights dict
        current_weights = self.get_current_weights()
        new_weights = dict(current_weights)

        for change in changes:
            if change.proposed_weight != change.current_weight:
                new_weights[change.signal_source] = change.proposed_weight

        # Generate report
        report = self._generate_report(changes, new_weights, dry_run)

        if dry_run:
            return RuleUpdateResult(
                version=new_version,
                changes=changes,
                applied=False,
                report=report,
                errors=[]
            )

        # Write new rule version
        try:
            with get_connection(self.db_path) as conn:
                cursor = conn.cursor()
                rule_params = json.dumps({"signal_weights": new_weights})
                cursor.execute(
                    """
                    INSERT INTO rule_versions (version, params_json, created_at, description)
                    VALUES (?, ?, ?, ?)
                    """,
                    (new_version, rule_params, datetime.utcnow().isoformat(),
                     f"Auto-updated from signal performance review (v{new_version-1} -> v{new_version})")
                )
                conn.commit()

            logger.info(f"Created rule version {new_version} with weights: {new_weights}")

        except Exception as e:
            error_msg = f"Failed to write rule version: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

        return RuleUpdateResult(
            version=new_version,
            changes=changes,
            applied=len(errors) == 0,
            report=report,
            errors=errors
        )

    def _generate_report(self, changes: list[RuleChange], new_weights: dict, dry_run: bool) -> str:
        """Generate markdown report."""
        mode = "DRY RUN" if dry_run else "APPLIED"
        lines = [
            f"📋 **Rule Updater Report ({mode})**",
            f"⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            f"📦 Version: {self.get_latest_version()}",
            "",
            "**Current Weights:**",
        ]

        for source, weight in sorted(new_weights.items()):
            lines.append(f"  {source}: {weight:.2f}")

        lines.append("")
        lines.append("**Proposed Changes:**")

        if not changes:
            lines.append("  (no changes)")
        else:
            for c in changes:
                if c.proposed_weight != c.current_weight:
                    arrow = "📈" if c.proposed_weight > c.current_weight else "📉"
                    lines.append(
                        f"  {arrow} {c.signal_source}: {c.current_weight:.2f} → {c.proposed_weight:.2f} "
                        f"({c.change_pct:+.1f}%) — {c.rationale}"
                    )
                else:
                    lines.append(f"  ➡️ {c.signal_source}: {c.current_weight:.2f} (unchanged) — {c.rationale}")

        return "\n".join(lines)

    def send_telegram(self, message: str) -> bool:
        """Send message via Telegram Bot API."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_HOME_CHANNEL") or os.getenv("TELEGRAM_CHAT_ID")

        if not token or not chat_id:
            logger.warning("Telegram not configured")
            return False

        try:
            import urllib.request
            import urllib.parse

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": "true",
            }).encode()

            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def run_update(self, dry_run: bool = True, lookback_days: int = 30) -> RuleUpdateResult:
        """Main entry point: propose and optionally apply changes."""
        logger.info(f"Running rule update (dry_run={dry_run})")

        changes = self.propose_changes(lookback_days)
        result = self.apply_changes(changes, dry_run)

        # Send report
        self.send_telegram(result.report)

        if result.applied:
            logger.info(f"Rule update applied: version {result.version}, {len(changes)} changes")
        else:
            logger.info(f"Rule update dry run: {len(changes)} proposed changes")

        return result


def create_updater(db_path: Optional[Path] = None) -> RuleUpdater:
    """Factory function."""
    return RuleUpdater(db_path=db_path)


def main() -> int:
    """CLI entry point for cron."""
    import argparse

    parser = argparse.ArgumentParser(description="Rule Updater Cron")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--lookback", type=int, default=30, help="Lookback days for metrics")
    args = parser.parse_args()

    try:
        updater = create_updater()
        result = updater.run_update(dry_run=not args.apply, lookback_days=args.lookback)
        return 0 if not result.errors else 1
    except Exception as e:
        logger.exception(f"Fatal error in rule updater: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())