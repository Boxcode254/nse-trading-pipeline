import os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path.home() / '.env')

import sys
sys.path.insert(0, str(Path.home() / '.trading'))
from trading.dashboard import create_dashboard

dashboard = create_dashboard()
report = dashboard.generate_text_report()
print(f'Report length: {len(report)} chars')
print(report)