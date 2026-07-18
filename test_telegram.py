import urllib.request
import urllib.parse

# Read actual token from .env
with open('/home/hermes/.env') as f:
    for line in f:
        if line.startswith('TELEGRAM_BOT_TOKEN='):
            token = line.strip().split('=', 1)[1]
        elif line.startswith('TELEGRAM_HOME_CHANNEL='):
            chat_id = line.strip().split('=', 1)[1]

print(f'Token: {token[:10]}...')
print(f'Chat: {chat_id}')

# Test getMe
url = f'https://api.telegram.org/bot{token}/getMe'
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as r:
    print('Bot:', r.read().decode())

# Test sendMessage
url2 = f'https://api.telegram.org/bot{token}/sendMessage'
data = urllib.parse.urlencode({
    'chat_id': chat_id,
    'text': 'Test message from Hermes dashboard cron',
    'parse_mode': 'Markdown',
}).encode()
req2 = urllib.request.Request(url2, data=data)
with urllib.request.urlopen(req2) as r:
    print('Send status:', r.status)
    print('Send response:', r.read().decode())