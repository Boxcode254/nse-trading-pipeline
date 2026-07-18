import os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path.home() / '.env')

import urllib.request

token = os.getenv('TELEGRAM_BOT_TOKEN')
chat_id = os.getenv('TELEGRAM_HOME_CHANNEL')

url = f'https://api.telegram.org/bot{token}/getMe'
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as r:
    print('Bot info:', r.read().decode())