"""Build a sandbox portfolio that mirrors the LIVE book shape (state.json).
Used by the auditor's independent verification. Never touches live state.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

LIVE_STATE = Path.home() / ".trading" / "portfolio" / "state.json"

LIVE_POSITIONS = [
    {"symbol": "ABSA", "avg_cost": 33.1863, "current_value": 10449.9, "shares": 306, "total_cost": 10155.02},
    {"symbol": "COOP", "avg_cost": 34.9466, "current_value": 5115.0, "shares": 132, "total_cost": 4612.95},
    {"symbol": "EABL", "avg_cost": 265.6667, "current_value": 4936.5, "shares": 18, "total_cost": 4782.0},
    {"symbol": "SCOM", "avg_cost": 35.5379, "current_value": 14411.7, "shares": 402, "total_cost": 14286.25},
    {"symbol": "KPLC", "avg_cost": 19.0522, "current_value": 9354.45, "shares": 453, "total_cost": 8630.65},
    {"symbol": "KCB", "avg_cost": 85.5826, "current_value": 8346.75, "shares": 93, "total_cost": 7959.18},
    {"symbol": "SCBK", "avg_cost": 339.6072, "current_value": 10019.5, "shares": 29, "total_cost": 9848.61},
    {"symbol": "BAMB", "avg_cost": 54.0, "current_value": 2106.0, "shares": 39, "total_cost": 2106.0},
    {"symbol": "TOTL", "avg_cost": 44.2275, "current_value": 3077.85, "shares": 71, "total_cost": 3140.15},
    {"symbol": "KNRE", "avg_cost": 3.5197, "current_value": 8270.64, "shares": 2188, "total_cost": 7701.0},
    {"symbol": "EQTY", "avg_cost": 87.2341, "current_value": 10647.0, "shares": 117, "total_cost": 10206.39},
]


def make_sandbox(prefix: str = "verify") -> Path:
    """Create a tmp sandbox portfolio dir with the live book shape. Returns dir."""
    d = Path(tempfile.mkdtemp(prefix=f"{prefix}_"))
    (d / "portfolio").mkdir()
    (d / "data").mkdir()
    state = {
        "cash": 16320.34,
        "created_at": "2026-07-13T13:52:30+03:00",
        "initial_capital": 100000.0,
        "max_drawdown_pct": 1.2681,
        "positions": [dict(p) for p in LIVE_POSITIONS],
        "updated_at": "2026-08-05T19:35:57+03:00",
    }
    with open(d / "portfolio" / "state.json", "w") as f:
        json.dump(state, f, indent=2)
    return d


if __name__ == "__main__":
    d = make_sandbox()
    print(d)
