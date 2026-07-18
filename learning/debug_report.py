from monthly_report import generate_monthly_report
from db import get_db

db = get_db()
report = generate_monthly_report(db, months=3)
print(report[:2000])