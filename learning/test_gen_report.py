import sys
sys.path.insert(0, '/home/hermes/.trading/learning')
from db import LearningDB, get_db
from monthly_report import generate_monthly_report

db = get_db()
report = generate_monthly_report(db, months_back=3)
print(report[:3000])