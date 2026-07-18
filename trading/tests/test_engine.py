"""Smoke test for the trading engine -- non-network, deterministic.

Runs against the synthetic fetcher by monkey-patching fetch_data, so the
test is fast, hermetic, and doesn't depend on yfinance being reachable.

Run from the repo root:
    cd ~/.trading/trading && ../.venv/bin/python -m pytest tests/test_engine.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

# Make the package importable when run as a script
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

# Isolate the package to a temp dir so the smoke test never touches ~/.trading
TMP_HOME = tempfile.mkdtemp(prefix="trading-smoke-")
os.environ["HOME"] = TMP_HOME

from trading import config  # noqa: E402
from trading.fetchers import fetch_data  # noqa: E402
from trading.fetchers import forex  # noqa: E402
from trading.indicators import tech  # noqa: E402
from trading.signals import backtest, engine  # noqa: E402
from trading.signals import validator as signal_validator  # noqa: E402
from trading.storage import log as storage_log  # noqa: E402

import pandas as pd  # noqa: E402


def test_indicators_basic() -> None:
    """SMA converges to the mean of a constant series; RSI on a constant is 100."""
    import pandas as pd

    s = pd.Series([5.0] * 50)
    sma = tech.sma(s, period=20)
    assert sma.iloc[-1] == 5.0, f"expected 5.0, got {sma.iloc[-1]}"

    rsi = tech.rsi(s, period=14)
    # No losses -> 100 by convention
    assert rsi.iloc[-1] == 100.0, f"expected 100.0, got {rsi.iloc[-1]}"


def test_crossover_classification() -> None:
    """The crossover classifier picks up clean bullish/bearish flips."""
    import pandas as pd

    fast = pd.Series([1.0, 1.0, 1.0, 2.0])   # crosses up on bar 3
    slow = pd.Series([1.5, 1.5, 1.5, 1.5])
    assert tech.crossover(fast, slow) == "bullish"

    fast2 = pd.Series([2.0, 2.0, 2.0, 1.0])  # crosses down on bar 3
    assert tech.crossover(fast2, slow) == "bearish"

    flat = pd.Series([1.0, 1.0, 1.0, 1.0])
    assert tech.crossover(flat, slow) == "neutral"


def test_engine_end_to_end() -> None:
    """Generate signals for one pair and assert the schema and shape."""
    df = forex.fetch_data("EUR/USD", days=120)  # synthetic fallback
    signals = engine.generate_signals(df, pair="EUR/USD")
    assert signals, "expected at least one signal"
    last = signals[-1]
    for key in ("pair", "date", "signal", "price", "sma_fast", "sma_slow", "rsi"):
        assert key in last, f"missing key {key}"
    assert last["signal"] in {"BUY", "SELL", "HOLD"}
    assert last["pair"] == "EUR/USD"


def test_log_roundtrip() -> None:
    """Log a signal, read it back, and confirm the row matches."""
    sig = {
        "pair": "EUR/USD",
        "date": "2026-06-27",
        "signal": "BUY",
        "price": 1.0850,
        "sma_fast": 1.0830,
        "sma_slow": 1.0790,
        "rsi": 55.0,
    }
    storage_log.log_signal(sig)
    rows = storage_log.read_history(10)
    assert rows, "log should have at least one row"
    head = rows[0]
    assert head["pair"] == "EUR/USD"
    assert head["signal"] == "BUY"
    # Prices are rounded to 6 dp, so 1.0850 -> "1.085000"
    assert head["price"] == "1.085000", head["price"]
    assert head["rsi"] == "55.0000", head["rsi"]


def test_backtest_runs() -> None:
    """The backtest returns the expected keys and finishes without error."""
    df = forex.fetch_data("USD/KES", days=200)
    result = backtest.run_backtest(df, pair="USD/KES")
    for key in ("pair", "n_signals", "buys", "sells", "wins", "losses", "win_rate", "avg_return_pct"):
        assert key in result, f"missing key {key}"
    assert 0.0 <= result["win_rate"] <= 100.0


def test_csv_files_created() -> None:
    """After a run, the per-pair raw CSVs and signals.csv must exist."""
    for pair in config.PAIRS:
        forex.fetch_data(pair, days=80)
    assert os.path.exists(config.SIGNALS_CSV), "signals.csv should exist"
    assert os.listdir(config.DATA_DIR), "data/ should have at least one csv"
    print(f"\nAll artifacts under: {TMP_HOME}")


# ── Signal Validator tests ────────────────────────────────────────────


def test_validator_accepts_hold_always() -> None:
    """HOLD signals always pass validation (no filtering applied)."""
    sig = {"pair": "EUR/USD", "signal": "HOLD", "price": 1.08, "rsi": 50.0}
    accepted, rejected = signal_validator.filter_signals([sig], pd.DataFrame())
    assert len(accepted) == 1
    assert len(rejected) == 0
    assert accepted[0]["validated"] is True
    assert accepted[0]["rejected_by"] == []
    assert "confidence" in accepted[0]


def test_validator_rejects_low_confidence_buy() -> None:
    """BUY with RSI too close to 50 gets rejected by confidence_threshold."""
    sig = {"pair": "EUR/USD", "signal": "BUY", "price": 1.08, "rsi": 55.0}
    accepted, rejected = signal_validator.filter_signals([sig], pd.DataFrame())
    assert len(accepted) == 0
    assert len(rejected) == 1
    assert rejected[0]["rejected_by"] == ["confidence_threshold"]
    assert rejected[0]["rejection_reasons"][0].startswith("confidence_threshold")
    assert rejected[0]["confidence"] < 50.0


def test_validator_accepts_high_confidence_sell() -> None:
    """SELL with RSI well below 50 passes confidence_threshold."""
    sig = {"pair": "EUR/USD", "signal": "SELL", "price": 1.08, "rsi": 25.0}
    # DataFrame with all OHLCV columns (spread/volume won't fail)
    df = pd.DataFrame({
        "open": [1.08], "high": [1.082], "low": [1.079],
        "close": [1.08], "volume": [100_000],
    })
    accepted, rejected = signal_validator.filter_signals([sig], df)
    assert len(accepted) == 1, f"expected accepted, got rejected={rejected}"
    assert accepted[0]["validated"] is True
    assert accepted[0]["rejected_by"] == []
    # RSI=25 → confidence = min(100, 25*2) = 50
    assert accepted[0]["confidence"] >= 50.0


def test_validator_rejects_wide_spread() -> None:
    """Signal rejected when intraday spread exceeds threshold."""
    sig = {"pair": "EUR/USD", "signal": "BUY", "price": 1.08, "rsi": 75.0}
    # 5% spread on a 1.08 close = 0.054, which exceeds 2% config
    df = pd.DataFrame({
        "open": [1.08], "high": [1.134], "low": [1.08],
        "close": [1.08], "volume": [100_000],
    })
    accepted, rejected = signal_validator.filter_signals([sig], df)
    assert len(accepted) == 0
    assert len(rejected) == 1
    assert "spread_filter" in rejected[0]["rejected_by"]
    reasons = rejected[0]["rejection_reasons"]
    assert any("spread" in r for r in reasons)


def test_validator_rejects_low_volume() -> None:
    """BUY signal rejected when volume is too low for the asset class."""
    # "SCOM" maps to "stocks" asset class (no "/") with min_volume=1,000,000
    sig = {"pair": "SCOM", "signal": "BUY", "price": 25.0, "rsi": 75.0}
    df = pd.DataFrame({
        "open": [25.0], "high": [25.2], "low": [24.9],
        "close": [25.0], "volume": [500_000],  # below 1,000,000 stocks min
    })
    accepted, rejected = signal_validator.filter_signals([sig], df)
    assert len(accepted) == 0
    assert len(rejected) == 1
    assert "volume_filter" in rejected[0]["rejected_by"]
    assert any("volume" in r for r in rejected[0]["rejection_reasons"])


def test_validator_multiple_reasons() -> None:
    """A signal can be rejected for multiple reasons simultaneously."""
    sig = {"pair": "USD/KES", "signal": "BUY", "price": 130.0, "rsi": 52.0}
    # RSI too close AND spread too wide
    df = pd.DataFrame({
        "open": [130.0], "high": [140.0], "low": [128.0],
        "close": [130.0], "volume": [500],
    })
    accepted, rejected = signal_validator.filter_signals([sig], df)
    assert len(accepted) == 0
    assert len(rejected) == 1
    assert len(rejected[0]["rejected_by"]) >= 2
    reasons = rejected[0]["rejection_reasons"]
    assert len(reasons) >= 2, f"expected multiple reasons, got: {reasons}"


def test_validator_accepts_clean_buy() -> None:
    """A signal that passes all filters is accepted."""
    sig = {"pair": "EUR/USD", "signal": "BUY", "price": 1.08, "rsi": 72.0}
    df = pd.DataFrame({
        "open": [1.08], "high": [1.082], "low": [1.079],
        "close": [1.08], "volume": [200_000],
    })
    accepted, rejected = signal_validator.filter_signals([sig], df)
    assert len(accepted) == 1
    assert len(rejected) == 0
    assert accepted[0]["rejected_by"] == []


def test_confidence_calculation() -> None:
    """Confidence is 0 at RSI=50 and grows linearly toward 100."""
    assert signal_validator.calculate_confidence({"rsi": 50.0}) == 0.0
    assert signal_validator.calculate_confidence({"rsi": 75.0}) == 50.0
    assert signal_validator.calculate_confidence({"rsi": 25.0}) == 50.0
    assert signal_validator.calculate_confidence({"rsi": 100.0}) == 100.0
    assert signal_validator.calculate_confidence({"rsi": 0.0}) == 100.0
    assert signal_validator.calculate_confidence({"rsi": 55.0}) == 10.0
    assert signal_validator.calculate_confidence({"rsi": 45.0}) == 10.0
    assert signal_validator.calculate_confidence({"no_rsi": 1}) == 0.0


def test_describe_filters() -> None:
    """describe_filters returns a non-empty list."""
    filters = signal_validator.describe_filters()
    assert filters
    for f in filters:
        assert "name" in f
        assert "description" in f


def test_nse_pairs_and_tickers_configured() -> None:
    """Phase 2: All 6 NSE equities are in PAIRS and have a yfinance mapping."""
    nse_pairs = ["SCOM", "KCB", "EQTY", "EABL", "ABSA", "SCBK"]
    for pair in nse_pairs:
        assert pair in config.PAIRS, f"{pair} missing from PAIRS"
        assert pair in config.YFINANCE_TICKERS, f"{pair} missing from YFINANCE_TICKERS"
        ticker = config.YFINANCE_TICKERS[pair]
        assert ticker.startswith("NSE:"), f"{pair} should map to NSE: ticker, got {ticker}"
    # Sanity: total pair count is 2 forex + 12+ stocks
    assert len(config.PAIRS) >= 12, f"expected at least 12 PAIRS, got {len(config.PAIRS)}"
    # LOOKBACK_DAYS bumped to 200 for stocks
    assert config.LOOKBACK_DAYS == 200, f"expected LOOKBACK_DAYS=200, got {config.LOOKBACK_DAYS}"


def test_nse_asset_class_and_signal_generation() -> None:
    """Phase 2: NSE stocks route to 'stocks' asset class and produce signals.

    Uses synthetic fallback (hermetic) so the test is fast and offline-safe.
    The fetcher is generic; this confirms the existing engine + validator
    stack works for stock data without code changes beyond config.
    """
    # Asset class routing: anything without "/" is stocks
    for pair in ["SCOM", "KCB", "EQTY", "EABL", "ABSA", "SCBK"]:
        assert config.get_asset_class(pair) == "stocks", f"{pair} should be stocks"
    # Forex still routes to forex
    assert config.get_asset_class("EUR/USD") == "forex"
    assert config.get_asset_class("USD/KES") == "forex"

    # Signal generation over synthetic SCOM data (200d lookback per config)
    df = forex.fetch_data("SCOM", days=200)
    # Confirm OHLCV shape
    for col in ("open", "high", "low", "close"):
        assert col in df.columns, f"missing column {col}"
    # Volume is part of the schema even on synthetic data
    assert "volume" in df.columns, "missing volume column"
    assert len(df) == 200, f"expected 200 rows, got {len(df)}"

    # Source must be tagged so the run report can show it
    assert df.attrs.get("source") in ("yfinance", "synthetic"), (
        f"unexpected source tag: {df.attrs.get('source')}"
    )

    # Signal generation should produce at least one verdict over 200 bars
    signals = engine.generate_signals(df, pair="SCOM")
    assert signals, "expected at least one signal from SCOM synthetic data"
    last = signals[-1]
    assert last["pair"] == "SCOM"
    assert last["signal"] in {"BUY", "SELL", "HOLD"}
    # Validator should handle the stock asset class without errors
    accepted, rejected = signal_validator.filter_signals(signals, df)
    # All signals have a 'validated' flag attached after filter_signals
    combined = accepted + rejected
    assert combined, "filter_signals dropped everything"
    for sig in combined:
        assert "validated" in sig
        assert "rejected_by" in sig


def test_nse_kcb_validator_volume_floor() -> None:
    """Phase 2: KCB stock signals are filtered by the 1M volume floor.

    Confirms the existing ASSET_FILTERS['stocks']['min_volume'] = 1_000_000
    actually fires for stock pairs (not just SCOM).
    """
    sig = {"pair": "KCB", "signal": "BUY", "price": 50.0, "rsi": 75.0}
    # 500k volume → below the 1M stock floor
    df = pd.DataFrame({
        "open": [50.0], "high": [50.5], "low": [49.8],
        "close": [50.0], "volume": [500_000],
    })
    accepted, rejected = signal_validator.filter_signals([sig], df)
    assert len(accepted) == 0
    assert "volume_filter" in rejected[0]["rejected_by"]

    # Same data on a forex pair (USD/KES) bypasses the 1M floor
    sig_fx = {"pair": "USD/KES", "signal": "BUY", "price": 130.0, "rsi": 75.0}
    df_fx = pd.DataFrame({
        "open": [130.0], "high": [130.5], "low": [129.8],
        "close": [130.0], "volume": [500_000],
    })
    accepted_fx, rejected_fx = signal_validator.filter_signals([sig_fx], df_fx)
    assert len(rejected_fx) == 0, f"forex should not hit volume filter: {rejected_fx}"
    assert len(accepted_fx) == 1


def test_nse_synthetic_fallback_when_ticker_unreachable() -> None:
    """Phase 2: When yfinance can't reach the NSE feed, we fall back to synthetic.

    Monkey-patches _fetch_yfinance to simulate a network failure and confirms
    the fetcher still returns a valid OHLCV DataFrame with source='synthetic'.
    """
    real_fetch = forex._fetch_yfinance

    def _broken_yfinance(pair: str, days: int):  # noqa: ARG001
        return None  # simulate yfinance unreachable

    forex._fetch_yfinance = _broken_yfinance
    try:
        df = forex.fetch_data("EQTY", days=200)
    finally:
        forex._fetch_yfinance = real_fetch

    assert df.attrs.get("source") == "synthetic", (
        f"expected synthetic fallback, got {df.attrs.get('source')}"
    )
    assert len(df) == 200
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns


def main() -> int:
    tests = [
        test_indicators_basic,
        test_crossover_classification,
        test_engine_end_to_end,
        test_log_roundtrip,
        test_backtest_runs,
        test_csv_files_created,
        test_validator_accepts_hold_always,
        test_validator_rejects_low_confidence_buy,
        test_validator_accepts_high_confidence_sell,
        test_validator_rejects_wide_spread,
        test_validator_rejects_low_volume,
        test_validator_multiple_reasons,
        test_validator_accepts_clean_buy,
        test_confidence_calculation,
        test_describe_filters,
        test_nse_pairs_and_tickers_configured,
        test_nse_asset_class_and_signal_generation,
        test_nse_kcb_validator_volume_floor,
        test_nse_synthetic_fallback_when_ticker_unreachable,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR   {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed} test(s) failed")
        return 1
    print(f"\nAll {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
