import os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path.home() / '.env')

import urllib.request
import urllib.parse

token = os.getenv('TELEGRAM_BOT_TOKEN')
chat_id = os.getenv('TELEGRAM_HOME_CHANNEL')

# Test without markdown
report = """Daily Trading Dashboard Report

Paper PnL:
Total Realized: $18,311.25
7-Day: $18,311.25
30-Day: $18,311.25
Open Positions: 0
Unrealized: $0.00
Win Rate: 94.4%
Avg Win: $1077.13
Avg Loss: $0.00
Profit Factor: 0.00
Max Drawdown: 0.0%

Signal Quality:
momentum: score=45.0 wr=100.0% dd=0.0% n=9 avg=3.65%
breakout: score=10.0 wr=100.0% dd=0.0% n=2 avg=2.61%
mean_reversion: score=10.0 wr=66.7% dd=0.0% n=3 avg=1.50%

Rule Versions:
v7 (2026-07-06): momentum=1.75 ACTIVE
v6 (2026-07-05): momentum=1.50
v5 (2026-07-05): momentum=1.25

Autonomy Status:
No trading crons detected"""

print(f'Report length: {len(report)} chars')

url = f'https://api.telegram.org/bot{token}/sendMessage'
data = urllib.parse.urlencode({
    'chat_id': chat_id,
    'text': report,
    # No parse_mode
    'disable_web_page_preview': 'true',
}).encode()

req = urllib.request.Request(url, data=data)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print('Status:', resp.status)
        print(resp.read().decode())
except Exception as e:
    print('Error:', e)
    import traceback
    traceback.print_exc()