#!/usr/bin/env bash
# cron_alert.sh — failure alerter for cron/scheduled scripts.
# Source this file, then use: cron_alert "script-name" "optional error text"
#
# Behavior:
#   - Sends a Telegram alert with timestamp, script name, and error text/code
#   - On missing token/chat, prints to stderr and exits 2
#   - Single shared implementation, one place to maintain

_CRON_ALERT_LOADED=1

cron_alert() {
  local script="${1:-unknown-script}"
  local error_text="${2:-command failed}"
  local token="${TELEGRAM_BOT_TOKEN:-}"
  local chat="${CRON_ALERT_CHAT_ID:-293971003}"
  local now
  now="$(date '+%Y-%m-%d %H:%M:%S %Z')" || now="unknown"

  if [ -z "$token" ]; then
    echo "[cron_alert] TELEGRAM_BOT_TOKEN not set; cannot send alert for $script" >&2
    return 2
  fi

  python3 - "$script" "$now" "$error_text" "$chat" "$token" >/dev/null 2>&1 <<'PY'
import json, sys, urllib.request, urllib.error
script, now, error_text, chat, token = sys.argv[1:6]
text = "\n".join([
    "🚨 *Cron failure alert*",
    f"`time`: {now}",
    f"`script`: {script}",
    "",
    "```",
    error_text[:3500],
    "```",
])
payload = json.dumps({
    "chat_id": chat,
    "text": text,
    "parse_mode": "Markdown",
    "disable_notification": False,
}).encode("utf-8")
req = urllib.request.Request(
    "https://api.telegram.org/bot" + token + "/sendMessage",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if '"ok":true' not in body:
            print("[cron_alert] telegram non-ok: " + body[:300], file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print("[cron_alert] Telegram HTTP " + str(e.code) + ": " + body[:300], file=sys.stderr)
    sys.exit(2)
except Exception as e:  # noqa: BLE001
    print("[cron_alert] Telegram send failed: " + str(e), file=sys.stderr)
    sys.exit(2)
PY
}

# Optional: cron_trap sets ERR+EXIT trap for the current shell.
# Usage: cron_trap "script-name"
#
# IMPORTANT: uses a global-style variable because `local` vars are not
# guaranteed in bash trap handlers. Callers must not assume it stays set.
cron_trap() {
  CRON_ALERT_TRAP_SCRIPT="${1:-bash-script}"
  trap 'cron_alert "$CRON_ALERT_TRAP_SCRIPT" "exit=$? last_cmd=$BASH_COMMAND" || true' ERR
  trap 'cron_alert "$CRON_ALERT_TRAP_SCRIPT" "exit=$? shell exited" || true' EXIT
}
