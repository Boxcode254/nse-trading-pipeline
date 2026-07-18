"""Outcome Reviewer Cron - hourly review of paper trades, closes expired, updates signal scores."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Load environment from ~/.env
from dotenv import load_dotenv
load_dotenv(Path.home() / ".env")

# Add trading to path
sys.path.insert(0, str(Path.home() / ".trading"))

from trading.learning.db import get_connection
from trading.paper_engine import PaperTradingEngine, create_engine
from trading.signal_profiler import SignalProfiler, create_profiler


# Setup logging
LOG_DIR = Path.home() / ".trading" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "outcome_reviewer.log"

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
class ReviewResult:
    """Result of a position review cycle."""
    positions_checked: int
    positions_closed: int
    total_realized_pnl: float
    closed_details: list[dict]
    errors: list[str]


@dataclass
class SignalTrend:
    """Signal performance trend."""
    signal_source: str
    previous_score: float
    current_score: float
    trend: str  # IMPROVING, DEGRADING, STABLE
    alert: bool


class PriceFeed:
    """Simple price feed - reads from existing prices.db (daily_closes table)."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (Path.home() / ".trading" / "learning" / "prices.db")

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get latest price for a symbol from daily_closes table."""
        try:
            with get_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT close_price FROM daily_closes WHERE symbol = ? ORDER BY date DESC LIMIT 1",
                    (symbol,)
                )
                row = cursor.fetchone()
                if row:
                    return float(row[0])
        except Exception as e:
            logger.warning(f"Price feed error for {symbol}: {e}")

        # Fallback: return None (will skip stop/target check, only check expiry)
        return None


class OutcomeReviewer:
    """Hourly reviewer for paper trading outcomes."""

    def __init__(
        self,
        decisions_db: Optional[Path] = None,
        prices_db: Optional[Path] = None,
        default_expiry_hours: int = 24,
    ):
        self.engine = create_engine(db_path=decisions_db, default_expiry_hours=default_expiry_hours)
        self.price_feed = PriceFeed(prices_db)
        self.profiler = create_profiler(db_path=decisions_db)

    def review_open_positions(self) -> ReviewResult:
        """Review all open positions, close expired/hit ones."""
        result = ReviewResult(
            positions_checked=0,
            positions_closed=0,
            total_realized_pnl=0.0,
            closed_details=[],
            errors=[]
        )

        # Get current prices for all open positions
        open_positions = self.engine.get_open_positions()
        result.positions_checked = len(open_positions)

        if not open_positions:
            logger.info("No open positions to review")
            return result

        # Build price dict
        prices = {}
        for pos in open_positions:
            price = self.price_feed.get_current_price(pos.symbol)
            if price:
                prices[pos.symbol] = price

        # Process events and close
        try:
            outcomes = self.engine.process_events_and_close(prices)
            result.positions_closed = len(outcomes)

            for outcome in outcomes:
                pnl = outcome.pnl_absolute
                result.total_realized_pnl += pnl
                
                # Get symbol from decision
                with get_connection(self.engine.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    decision = conn.execute(
                        "SELECT symbol FROM decisions WHERE id = ?",
                        (outcome.decision_id,)
                    ).fetchone()
                    symbol = decision["symbol"] if decision else str(outcome.decision_id)
                
                result.closed_details.append({
                    "symbol": symbol,
                    "exit_price": outcome.exit_price,
                    "pnl_absolute": round(pnl, 2),
                    "pnl_pct": round(outcome.pnl_pct, 2),
                    "reason": outcome.exit_reason.value,
                    "hold_minutes": outcome.hold_duration_minutes,
                })
                logger.info(
                    f"Closed: {symbol} "
                    f"PnL={pnl:.2f} ({outcome.pnl_pct:.2f}%) "
                    f"Reason={outcome.exit_reason.value}"
                )
        except Exception as e:
            error_msg = f"Error processing events: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)

        return result

    def calculate_signal_performance(self, lookback_days: int = 30) -> dict:
        """Get updated signal metrics and identify trends."""
        metrics = self.profiler.get_all_signal_metrics(lookback_days)

        # For now, just return current metrics (no historical comparison yet)
        return {
            m.signal_source: {
                "consistency_score": m.consistency_score,
                "win_rate": m.win_rate,
                "max_drawdown_pct": m.max_drawdown_pct,
                "executed_signals": m.executed_signals,
                "avg_pnl_pct": m.avg_pnl_pct,
                "sharpe_like": m.sharpe_like,
            }
            for m in metrics
        }

    def generate_report(self, review_result: ReviewResult, signal_perf: dict) -> str:
        """Generate markdown report for Telegram."""
        lines = [
            "📊 **Hourly Paper Trading Review**",
            f"⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"🔍 **Positions Checked:** {review_result.positions_checked}",
            f"✅ **Positions Closed:** {review_result.positions_closed}",
            f"💰 **Realized PnL:** KES {review_result.total_realized_pnl:.2f}",
        ]

        if review_result.closed_details:
            lines.append("")
            lines.append("**Closed Trades:**")
            for d in review_result.closed_details:
                emoji = "🟢" if d["pnl_absolute"] >= 0 else "🔴"
                lines.append(
                    f"{emoji} Decision {d['symbol']}: "
                    f"KES {d['pnl_absolute']:.2f} ({d['pnl_pct']:.2f}%) "
                    f"| {d['reason']} | {d['hold_minutes']}min"
                )

        if signal_perf:
            lines.append("")
            lines.append("**Signal Scores:**")
            for source, perf in sorted(
                signal_perf.items(),
                key=lambda x: x[1]["consistency_score"],
                reverse=True
            )[:5]:
                lines.append(
                    f"  {source}: score={perf['consistency_score']:.1f} "
                    f"wr={perf['win_rate']:.1f}% dd={perf['max_drawdown_pct']:.1f}%"
                )

        if review_result.errors:
            lines.append("")
            lines.append("⚠️ **Errors:**")
            for err in review_result.errors:
                lines.append(f"  - {err}")

        return "\n".join(lines)

    def send_telegram(self, message: str) -> bool:
        """Send message via Telegram Bot API."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_HOME_CHANNEL") or os.getenv("TELEGRAM_CHAT_ID")

        if not token or not chat_id:
            logger.warning("Telegram not configured (missing token/chat_id)")
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

    def run_hourly_review(self) -> None:
        """Main entry point for cron - runs full review cycle."""
        logger.info("Starting hourly outcome review")

        # 1. Review and close positions
        review_result = self.review_open_positions()

        # 2. Calculate signal performance
        signal_perf = self.calculate_signal_performance()

        # 3. Generate report
        report = self.generate_report(review_result, signal_perf)

        # 4. Send to Telegram
        self.send_telegram(report)

        # 5. Log summary
        logger.info(
            f"Review complete: checked={review_result.positions_checked} "
            f"closed={review_result.positions_closed} "
            f"pnl=KES {review_result.total_realized_pnl:.2f} "
            f"signals={len(signal_perf)}"
        )


def main() -> int:
    """CLI entry point for cron."""
    try:
        reviewer = OutcomeReviewer()
        reviewer.run_hourly_review()
        return 0
    except Exception as e:
        logger.exception(f"Fatal error in outcome reviewer: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())