import json, csv, os
base = os.path.expanduser("~/.trading")

# --- signals.csv latest date + last 3 rows ---
sig_path = os.path.join(base, "signals.csv")
rows = []
with open(sig_path, newline="") as f:
    r = csv.reader(f)
    header = next(r)
    for row in r:
        rows.append(row)
print("SIGNALS_HEADER:", header)
print("SIGNALS_TOTAL:", len(rows))
print("SIGNALS_LAST3:", json.dumps(rows[-3:]))
# count today's (2026-08-28) and yesterday's (2026-08-27)
from collections import Counter
dates = Counter(r[0][:10] for r in rows if r and r[0])
print("SIGNALS_LAST5_DATES:", json.dumps(dict(list(sorted(dates.items()))[-5:])))

# --- state.json TOTL + cash + updated_at ---
st = json.load(open(os.path.join(base, "portfolio", "state.json")))
print("STATE_CASH:", st.get("cash"))
print("STATE_UPDATED_AT:", st.get("updated_at"))
for p in st.get("positions", []):
    if p.get("symbol") == "TOTL":
        print("STATE_TOTL:", json.dumps(p))

# --- benchmark snapshot interpretation ---
bm = json.load(open(os.path.join(base, "portfolio", "benchmark.json")))
print("BENCHMARK_LAST_SNAPSHOT:", json.dumps(bm["snapshots"][-1]))
print("BENCHMARK_N_SNAPSHOTS:", len(bm["snapshots"]))
print("BENCHMARK_ASSETS:", bm["assets"])
