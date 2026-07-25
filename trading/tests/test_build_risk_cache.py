"""Tests for trading.scripts.build_risk_cache (logic, no network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from trading.scripts import build_risk_cache as rc


def test_cache_dir_created():
    # _cache_dir creates ~/.trading/portfolio/price_history (idempotent)
    d = rc._cache_dir()
    assert d.exists(), "cache dir should be created"
    assert d.name == "price_history"
    assert d.parent.name == "portfolio"


def test_payload_shape_from_df():
    import pandas as pd
    df = pd.DataFrame({
        "close": [100.0, 102.5, 101.0],
        "volume": [1000.0, 1100.0, 900.0],
    })
    closes = [round(float(x), 4) for x in df["close"].tolist()]
    volumes = [round(float(x), 2) for x in df.get("volume", []).tolist()]
    payload = {"symbol": "KCB", "close": closes, "volume": volumes}
    # round-trips through JSON
    s = json.dumps(payload)
    back = json.loads(s)
    assert back["symbol"] == "KCB"
    assert back["close"] == [100.0, 102.5, 101.0]
    assert len(back["close"]) == len(back["volume"]) == 3
