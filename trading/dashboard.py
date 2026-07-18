"""Supervision Dashboard — ARCHIVED 2026-07-17.

The paper engine system this dashboard reported on has been retired.
It was a separate, disconnected trading system running alongside the
auto-trader with no shared state. The dual Telegram reporting created
ambiguity about which portfolio numbers were real.

For portfolio status, the auto-trader system (auto_trader.py + state.json)
is the authoritative source. See ~/.trading/archive/ for preserved code.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path.home() / ".env")

sys.path.insert(0, str(Path.home() / ".trading"))

__all__ = ["Dashboard", "create_dashboard", "PnLSummary"]

LOG_DIR = Path.home() / ".trading" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "dashboard.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class PnLSummary:
    """PnL summary — archived, always returns zeros."""
    total_realized: float = 0.0
    realized_7d: float = 0.0
    realized_30d: float = 0.0
    open_positions: int = 0
    unrealized_pnl: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0


class Dashboard:
    """Archived — paper engine system retired 2026-07-17."""

    ARCHIVE_NOTE = (
        "📋 **Supervision Dashboard — Archived**\n\n"
        "The paper engine system this dashboard reported on has been retired "
        "(2026-07-17). It was a separate, disconnected trading system running "
        "alongside the auto-trader with no shared state.\n\n"
        "**Portfolio status is available from the auto-trader system.**\n"
        "Run the auto-trader at 10:30 EAT for the latest verified numbers."
    )

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path

    def get_paper_pnl_summary(self) -> PnLSummary:
        """Archived."""
        return PnLSummary()

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Archived."""
        return None

    def get_signal_quality(self) -> list[dict]:
        """Archived."""
        return []

    def get_rule_versions(self) -> list:
        """Archived."""
        return []

    def get_autonomy_status(self) -> dict:
        """Archived."""
        return {
            "auto_trading_enabled": False,
            "last_human_review": "System archived 2026-07-17",
            "rule_versions": [],
            "current_weights": {},
        }

    @staticmethod
    def _friendly_name(source: str) -> tuple[str, str]:
        return source, ""

    @staticmethod
    def _total_outcomes(db_path=None) -> tuple[int, int, int]:
        return 0, 0, 0

    @staticmethod
    def _get_real_portfolio_value() -> dict:
        """Get portfolio snapshot from state.json (auto-trader system)."""
        try:
            path = Path.home() / ".trading" / "portfolio" / "state.json"
            if path.exists():
                with open(path) as f:
                    d = json.load(f)
                total_cash = d.get("cash", 0)
                positions = d.get("positions", [])
                total_shares = sum(p.get("shares", 0) for p in positions)
                invested = sum(p.get("shares", 0) * p.get("avg_cost", 0) for p in positions)
                portfolio_value = total_cash + invested
                return {
                    "value": round(portfolio_value, 2),
                    "cash": round(total_cash, 2),
                    "invested": round(invested, 2),
                    "positions": len(positions),
                    "total_shares": total_shares,
                    "updated": d.get("updated_at", "unknown"),
                    "source": "state.json (auto-trader system)",
                }
            return {"value": 0, "cash": 0, "invested": 0, "positions": 0,
                    "total_shares": 0, "source": "no state.json"}
        except Exception as e:
            return {"value": 0, "cash": 0, "invested": 0, "positions": 0,
                    "source": f"error: {e}"}

    def generate_text_report(self) -> str:
        """Generate archive-status report with real portfolio from state.json."""
        pv = self._get_real_portfolio_value()
        return (
            f"{self.ARCHIVE_NOTE}\n\n"
            f"### 📊 Real Portfolio (from auto-trader state.json)\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| **Portfolio Value** | KES {pv['value']:,.2f} |\n"
            f"| **Cash** | KES {pv['cash']:,.2f} |\n"
            f"| **Invested** | KES {pv['invested']:,.2f} |\n"
            f"| **Positions** | {pv['positions']} |\n"
            f"| **Total Shares** | {pv['total_shares']:,} |\n"
            f"| **Updated** | {pv['updated']} |\n"
            f"| **Source** | {pv['source']} |\n"
        )

    def generate_html_report(self) -> str:
        """Minimal HTML showing archive status."""
        pv = self._get_real_portfolio_value()
        return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Dashboard — Archived</title>
<style>body{{font-family:sans-serif;max-width:800px;margin:2rem auto;padding:1rem}}
.archived{{color:#888;font-size:0.9em}}.value{{font-weight:bold;font-size:1.2em}}</style>
</head><body>
<h1>📋 Supervision Dashboard — Archived</h1>
<p class="archived">Paper engine system retired 2026-07-17.</p>
<p>Portfolio status is available from the auto-trader system.</p>
<h2>Real Portfolio (state.json)</h2>
<table border="1" cellpadding="6">
<tr><td>Portfolio Value</td><td class="value">KES {pv['value']:,.2f}</td></tr>
<tr><td>Cash</td><td>KES {pv['cash']:,.2f}</td></tr>
<tr><td>Invested</td><td>KES {pv['invested']:,.2f}</td></tr>
<tr><td>Positions</td><td>{pv['positions']}</td></tr>
<tr><td>Updated</td><td>{pv['updated']}</td></tr>
</table>
<p class="archived">Last updated: {pv['updated']} · Source: {pv['source']}</p>
</body></html>"""

    def send_telegram(self, message: str) -> bool:
        """Send message via Telegram Bot API."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_HOME_CHANNEL") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logger.warning("Telegram not configured")
            return False
        try:
            import urllib.request, urllib.parse
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": chat_id, "text": message,
                "parse_mode": "Markdown", "disable_web_page_preview": "true",
            }).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def run_report(self, send_telegram: bool = True) -> str:
        """Generate and optionally send archive status report."""
        content = self.generate_text_report()
        if send_telegram:
            sent = self.send_telegram(content)
            logger.info(f"Report {'sent' if sent else 'FAILED'} to Telegram")
        return content


def create_dashboard(db_path: Optional[Path] = None) -> Dashboard:
    """Factory function."""
    return Dashboard(db_path=db_path)


def main() -> int:
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Trading Dashboard (Archived)")
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--output", type=str)
    args = parser.parse_args()
    try:
        dashboard = create_dashboard()
        if args.html:
            content = dashboard.generate_html_report()
            if args.output:
                Path(args.output).write_text(content)
                logger.info(f"HTML written to {args.output}")
            else:
                print(content)
        else:
            content = dashboard.run_report(send_telegram=not args.no_telegram)
            if not args.no_telegram:
                print("Report sent to Telegram")
            else:
                print(content)
        return 0
    except Exception as e:
        logger.exception(f"Dashboard error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
