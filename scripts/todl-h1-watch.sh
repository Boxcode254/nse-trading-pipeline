#!/bin/bash
# todl-h1-watch.sh — one-shot: fetch TotalEnergies Kenya H1-2026 results on/after 28 Aug 2026
# and classify into fundamentals.json. Auto-closes the last NO_DATA row in the audit.
# Runs as a one-shot cron; exits clean (no output) if results not yet published.

set -uo pipefail
cd ~/.trading || exit 1

# Env for any needed keys (none required for web but keep parity)
if [ -f "$HOME/.hermes/.env" ]; then
  set -a; . "$HOME/.hermes/.env"; set +a
fi

PY=.venv/bin/python
OUT=/tmp/todl_h1_$$.log

# 1. Check if TOTL H1-2026 has been published yet. Guardian: if the fundamentals
#    row already has a verified H1-2026 figure, this is already done — exit quiet.
if "$PY" -c "import json;f=json.load(open('news/fundamentals.json'));r=f.get('TOTL',{});print('DONE' if r.get('as_of')=='2026-08-28' else 'PENDING')" 2>/dev/null | grep -q DONE; then
  echo "[SILENT]"
  exit 0
fi

# 2. Fetch the announcement. TOTL publishes H1 results on ~28 Aug 2026.
#    Try the strong sources; collect headline figures via the search+extract.
echo "Fetching TotalEnergies Marketing Kenya H1-2026 results (target 28 Aug 2026)" > "$OUT"

found=$(curl -s "https://r.jina.ai/https://kenyanwallstreet.com/totalenergies-kenya-h1-profit-up-17-to-ksh-1-1bn-lower-costs-cushion-revenue-dip" -H "Accept: text/plain" 2>/dev/null || true)

# If that specific URL was stale, also try a fresher one for the 28 Aug release.
if ! echo "$found" | grep -qiE 'H1 2026|2026'; then
  found="$found $(curl -s "https://r.jina.ai/https://www.the-star.co.ke/news/2026-08-28" -H "Accept: text/plain" 2>/dev/null || true)"
fi
export TOTL_FETCH="$found"

# 3. If we find H1-2026 mention with figures, write the fundamentals row.
#    We flag the AS-OF date = 2026-08-28 (the one-shot's target publication).
"$PY" - <<'PYEOF' >> "$OUT" 2>&1
import json, re, sys, os
from datetime import datetime, timezone

path = 'news/fundamentals.json'
fund = json.load(open(path))
row = fund.get('TOTL', {})

# If already populated for H1-2026, nothing to do.
if row.get('as_of') == '2026-08-28' and row.get('pat', {}).get('latest_bn'):
    print("TOTL already classified for H1-2026. Nothing to do.")
    sys.exit(0)

# Read the fetched text for H1-2026 PAT. We do not fabricate; if the exact
# H1-2026 figure is not present, leave NO_DATA and print a clear PENDING so
# the human can see it genuinely is not yet published.
raw = os.environ.get("TOTL_FETCH", "")

# STRICT heuristic — prevents the stale-page misfire that wrote H1-2025 as H1-2026.
# Accept only if BOTH hold:
#   (a) the profit figure is attributed to H1 2026 / half year 2026 / June 2026,
#   (b) the page is NOT primarily an old (2025) result.
# A bare '2026' anywhere in the page (chrome/date-stamp) is NOT enough.
announced = False
recent_period = re.search(r'(H1|half[- ]?year|six months)[^.]{0,60}2026', raw, re.I) or \
                re.search(r'2026[^.]{0,40}(H1|half[- ]?year)', raw, re.I)
old_period = re.search(r'(H1|half[- ]?year)[^.]{0,60}2025', raw, re.I)

# Figure looks like "profit after tax of KSh 1.2 bn" or "net profit ... to KSh 2.3 billion"
m = re.search(r'(profit after tax|net profit|profit)\s*(?:after tax)?[^0-9]{0,30}(\d+[.,]\d*)\s*(bn|billion|mn|million)', raw, re.I)

if m and recent_period and not old_period:
    latest = float(m.group(2).replace(',', ''))
    fund['TOTL'] = {
        "as_of": "2026-08-28",
        "is_peer": False,
        "period": "H1-2026",
        "pat": {"latest_bn": latest, "yoy_pct": None, "period": "H1-2026"},
        "confidence": "med",
        "notes": f"TotalEnergies Marketing Kenya H1-2026, auto-fetched {datetime.now(timezone.utc).strftime('%Y-%m-%d')}. Published 28 Aug 2026. Verify YoY before treating as signal."
    }
    announced = True
else:
    print(f"TOTL H1-2026 NOT yet confirmed published (period_match={bool(recent_period)}, old_match={bool(old_period)}, figure_match={bool(m)}). Staying NO_DATA. Not fabricating.")

if announced:
    json.dump(fund, open(path, 'w'), indent=2)
    print(f"TOTL H1-2026 recorded: PAT {latest}bn. Classify and confirm.")
PYEOF

# 4. Classify and report
if "$PY" -c "import json;f=json.load(open('news/fundamentals.json'));print(f.get('TOTL',{}).get('as_of',''))" | grep -q 2026-08-28; then
  echo "=== TOTL classified ==="
  "$PY" scripts/fundamental_audit_run.py 2>&1 | tail -5
else
  echo "=== TOTL still pending publication ==="
fi
