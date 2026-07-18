import sys
import os
sys.path.insert(0, '/home/hermes/.trading/learning')

# Import db module first
from db import LearningDB, MonthlyStats, get_db

# Then import monthly_report
from monthly_report import main

import sys
sys.exit(main(months=3, quiet=True))