#!/usr/bin/env python3
"""Test the exact acceptance criteria from the task."""

import sys
sys.path.insert(0, '/home/hermes/.trading')

# Test 1: python3 -c 'from trading.learning.db import init_db; init_db()'
print("Test 1: from trading.learning.db import init_db; init_db()")
try:
    from trading.learning.db import init_db
    init_db()
    print("  PASSED")
except Exception as e:
    print(f"  FAILED: {e}")
    sys.exit(1)

# Test 2: Verify schema
print("\nTest 2: sqlite3 ~/.trading/learning/decisions.db '.schema'")
import subprocess
result = subprocess.run(
    ["sqlite3", "/home/hermes/.trading/learning/decisions.db", ".schema"],
    capture_output=True,
    text=True
)
if result.returncode == 0:
    print("  PASSED")
    print(result.stdout[:500])
else:
    print(f"  FAILED: {result.stderr}")
    sys.exit(1)

# Test 3: Check all three tables exist
tables = ["decisions", "outcomes", "rule_versions"]
for table in tables:
    if table in result.stdout:
        print(f"  Table '{table}': EXISTS ✓")
    else:
        print(f"  Table '{table}': MISSING ✗")
        sys.exit(1)

print("\nAll acceptance criteria PASSED!")