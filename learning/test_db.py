import sys
sys.path.insert(0, '/home/hermes/.trading/learning')
from db import LearningDB
db = LearningDB()
print('DB initialized:', db.db_path)

# Test adding a recommendation
from db import Recommendation
rec = Recommendation(
    symbol="AAPL",
    date="2026-07-01",
    confidence=0.85,
    recommendation="BUY",
    score=78.5,
    factors={"rsi": 45, "macd": "bullish", "volume": "high"}
)
rec_id = db.add_recommendation(rec)
print(f"Added recommendation: {rec_id}")

# Test adding an outcome
from db import Outcome
outcome = Outcome(
    symbol="AAPL",
    date="2026-07-01",
    market_outcome="UP",
    expected_return=5.2,
    actual_return=4.8,
    time_to_target=12,
    success=True
)
outcome_id = db.add_outcome(outcome)
print(f"Added outcome: {outcome_id}")

# Test stats
stats = db.get_overall_stats()
print("Overall stats:", stats)

monthly = db.get_monthly_stats()
print("Monthly stats:", monthly)