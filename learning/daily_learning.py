"""Daily Learning Cron Job - Runs after morning briefing.

Logs recommendations, records daily prices, evaluates outcomes,
calculates monthly performance, and delivers report to Telegram.
"""

import sys
import os
from datetime import datetime, date
from pathlib import Path

# Add trading root to path
TRADING_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(TRADING_ROOT))

# Import from local learning package
sys.path.insert(0, str(Path(__file__).parent))

from db import LearningDB, get_db
from outcomes import get_recorder


def record_daily_recommendations(db: LearningDB) -> int:
    """Log today's recommendations from the ranking service."""
    try:
        from trading.services import ranking as ranking_svc
        result = ranking_svc.build()
        ranked = result.get("ranked", [])
        
        count = 0
        today = date.today().strftime("%Y-%m-%d")
        
        for entry in ranked:
            symbol = entry.get("symbol")
            score = entry.get("score", 0)
            recommendation = entry.get("recommendation", "HOLD")
            factors = entry.get("factors", {})
            
            # Map recommendation to confidence
            conf_map = {
                "Strong Buy": 0.9,
                "Buy": 0.8,
                "Accumulate": 0.7,
                "Hold": 0.5,
                "Reduce": 0.4,
                "Avoid": 0.3,
                "Strong Sell": 0.2,
            }
            confidence = conf_map.get(recommendation, 0.5)
            
            # Skip HOLD recommendations for learning (focus on actionable)
            if recommendation in ("Hold", "HOLD"):
                continue
            
            # Create recommendation
            from db import Recommendation
            rec = Recommendation(
                symbol=symbol,
                date=today,
                confidence=confidence,
                recommendation=recommendation.upper(),
                score=score,
                factors=factors,
            )
            
            # Upsert (dedup by factors hash)
            db.upsert_recommendation(rec)
            count += 1
        
        print(f"📝 Logged {count} actionable recommendations")
        return count
        
    except Exception as e:
        print(f"⚠️  Failed to log recommendations: {e}")
        return 0


def record_daily_prices(recorder) -> int:
    """Fetch and record today's closing prices."""
    try:
        from trading import config
        symbols = config.PAIRS
        
        results = recorder.record_daily_closes_from_market_service(symbols)
        print(f"💰 Recorded {len(results)} daily closing prices")
        return len(results)
        
    except Exception as e:
        print(f"⚠️  Failed to record prices: {e}")
        return 0


def evaluate_pending_outcomes(recorder) -> int:
    """Evaluate all pending outcomes using recorded price data."""
    try:
        eval_date = date.today().strftime("%Y-%m-%d")
        outcomes = recorder.evaluate_all_pending(eval_date, max_holding_days=90)
        print(f"📊 Evaluated {len(outcomes)} pending outcomes")
        
        for o in outcomes:
            status = "✅" if o.success else "❌"
            print(f"   {status} {o.symbol} {o.recommendation_type}: {o.actual_return_pct:.2f}% in {o.time_to_target_days}d")
        
        return len(outcomes)
        
    except Exception as e:
        print(f"⚠️  Failed to evaluate outcomes: {e}")
        return 0


def send_monthly_report_if_needed(db: LearningDB) -> bool:
    """Generate and send monthly report on the 1st of each month."""
    today = date.today()
    if today.day != 1:
        return False
    
    try:
        from monthly_report import generate_monthly_report
        
        # Generate report for last month
        from datetime import timedelta
        last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        
        report = generate_monthly_report(db, months_back=1, include_details=True)
        
        # Save to logs
        log_path = Path.home() / ".trading" / "logs" / f"monthly-report-{last_month}.md"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(report)
        
        # Send to Telegram
        from trading.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        import urllib.request
        import urllib.parse
        
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": f"📊 *Monthly Trading Report — {last_month}*\n\n{report[:3500]}",
                "parse_mode": "Markdown",
            }
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
            urllib.request.urlopen(req, timeout=10)
            print(f"📤 Sent monthly report to Telegram")
        
        print(f"📋 Monthly report generated: {log_path}")
        return True
        
    except Exception as e:
        print(f"⚠️  Failed to generate/send monthly report: {e}")
        return False


def main() -> int:
    print("=" * 60)
    print(f"🤖 DAILY LEARNING JOB — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Initialize database
    db = get_db()
    recorder = get_recorder()
    
    # Import config
    from trading import config
    
    # 1. Log today's recommendations
    print("\n📝 Logging recommendations...")
    record_daily_recommendations(db)
    
    # 2. Record daily closing prices
    print("\n💰 Recording daily prices...")
    from outcomes import record_daily_closes_from_market_service
    record_daily_closes_from_market_service(config.PAIRS)
    
    # 3. Evaluate pending outcomes
    print("\n📊 Evaluating outcomes...")
    evaluate_pending_outcomes(recorder)
    
    # 4. Monthly report (1st of month)
    print("\n📋 Checking for monthly report...")
    send_monthly_report_if_needed(db)
    
    print("\n" + "=" * 60)
    print("✅ Daily learning job complete")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())