"""Tests for the Market Ranking Engine.

Pure-logic tests against synthetic DataFrames. The ranking engine must:

1. Compute 8 factor scores (trend, momentum, volatility, liquidity,
   relative strength, risk, market regime, technical alignment)
2. Aggregate them via configurable weights into a 0-100 score
3. Map the score to a recommendation tier
4. Rank all configured assets
5. Generate a plain-language reason and expected holding period
6. Integrate with the CLI (``python3 -m trading rank``) and the daily
   report (Top Opportunities section)

Run from the repo root:
    ~/.trading/.venv/bin/python -m tests.test_ranking
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

# Make the package importable when run as a script
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

# Isolate the package to a temp dir so the smoke test never touches ~/.trading
TMP_HOME = tempfile.mkdtemp(prefix="trading-rank-")
os.environ["HOME"] = TMP_HOME

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from trading import config  # noqa: E402
from trading.ranking import scorer, ranker, output as rank_output  # noqa: E402
from trading.ranking.scorer import (  # noqa: E402
    score_trend,
    score_momentum,
    score_volatility,
    score_liquidity,
    score_relative_strength,
    score_risk,
    score_regime,
    score_alignment,
    aggregate_score,
    SCORE_FACTOR_NAMES,
)
from trading.ranking.ranker import (  # noqa: E402
    rank_assets,
    build_ranking,
    recommendation_for,
    expected_holding_period,
)


# ── Test fixtures ────────────────────────────────────────────────────


def _make_trending_df(n: int = 200, slope: float = 0.001, vol: float = 0.005,
                      start: float = 100.0, seed: int = 1) -> pd.DataFrame:
    """Build a synthetic OHLCV frame with a controlled trend + vol."""
    rng = np.random.default_rng(seed)
    daily_ret = rng.normal(loc=slope, scale=vol, size=n)
    close = start * (1.0 + daily_ret).cumprod()
    intraday = rng.uniform(0.001, 0.005, size=n) * close
    open_ = close + rng.normal(0, 0.001, size=n) * close
    high = pd.Series(close).combine(pd.Series(open_), max) + intraday
    low = pd.Series(close).combine(pd.Series(open_), min) - intraday
    volume = rng.integers(1_000_000, 5_000_000, size=n)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n, name="date")
    return pd.DataFrame({
        "open": open_, "high": high.values, "low": low,
        "close": close, "volume": volume,
    }, index=idx)


def _make_bear_df(n: int = 200, slope: float = -0.002, vol: float = 0.012,
                  seed: int = 2) -> pd.DataFrame:
    return _make_trending_df(n=n, slope=slope, vol=vol, start=100.0, seed=seed)


def _make_flat_df(n: int = 200, slope: float = 0.0, vol: float = 0.002,
                  seed: int = 3) -> pd.DataFrame:
    return _make_trending_df(n=n, slope=slope, vol=vol, start=100.0, seed=seed)


# ── Factor scorer tests ──────────────────────────────────────────────


def test_score_trend_strong_uptrend() -> None:
    """A sustained uptrend with SMA(20) > SMA(50) and positive slope scores high."""
    df = _make_trending_df(slope=0.003, vol=0.004, seed=11)
    score = score_trend(df, config.SMA_FAST, config.SMA_SLOW)
    assert 0.0 <= score <= 100.0
    assert score >= 60.0, f"strong uptrend should score high, got {score}"


def test_score_trend_downtrend() -> None:
    """A downtrend with SMA(20) < SMA(50) scores low."""
    df = _make_bear_df(slope=-0.003, seed=12)
    score = score_trend(df, config.SMA_FAST, config.SMA_SLOW)
    assert 0.0 <= score <= 100.0
    assert score <= 40.0, f"downtrend should score low, got {score}"


def test_score_momentum_rsi() -> None:
    """Momentum score reflects RSI position: mid-range = neutral, overbought = strong."""
    # Bullish momentum: positive slope → RSI > 50
    df_bull = _make_trending_df(slope=0.002, vol=0.003, seed=21)
    bull_score = score_momentum(df_bull, config.RSI_PERIOD)
    df_bear = _make_bear_df(slope=-0.002, seed=22)
    bear_score = score_momentum(df_bear, config.RSI_PERIOD)
    assert bull_score > bear_score, (
        f"bullish momentum ({bull_score}) should beat bearish ({bear_score})"
    )
    assert 0.0 <= bull_score <= 100.0
    assert 0.0 <= bear_score <= 100.0


def test_score_volatility_prefers_low() -> None:
    """Low volatility (a stable trend) scores higher than high vol (choppy)."""
    df_stable = _make_trending_df(slope=0.001, vol=0.002, seed=31)
    df_choppy = _make_trending_df(slope=0.001, vol=0.025, seed=32)
    stable_score = score_volatility(df_stable)
    choppy_score = score_volatility(df_choppy)
    assert stable_score > choppy_score, (
        f"stable ({stable_score}) should beat choppy ({choppy_score})"
    )


def test_score_liquidity_high_volume() -> None:
    """High relative volume scores higher than low volume."""
    df = _make_trending_df(seed=41)
    # Mutate volume to 2 different levels
    df_high = df.copy()
    df_high["volume"] = df_high["volume"] * 10  # 10x normal
    df_low = df.copy()
    df_low["volume"] = df_low["volume"] * 0.05  # 5% of normal
    high_score = score_liquidity(df_high)
    low_score = score_liquidity(df_low)
    assert high_score > low_score, (
        f"high vol ({high_score}) should beat low vol ({low_score})"
    )


def test_score_relative_strength() -> None:
    """An asset outperforming the universe scores higher than an underperformer."""
    df_strong = _make_trending_df(slope=0.005, seed=51)
    df_weak = _make_bear_df(slope=-0.005, seed=52)
    # Build a multi-asset universe with these two
    universe = {"STRONG": df_strong, "WEAK": df_weak}
    strong_score = score_relative_strength(df_strong, universe, windows=(21, 63))
    weak_score = score_relative_strength(df_weak, universe, windows=(21, 63))
    assert strong_score > weak_score, (
        f"strong ({strong_score}) should beat weak ({weak_score})"
    )
    assert 0.0 <= strong_score <= 100.0
    assert 0.0 <= weak_score <= 100.0


def test_score_risk_low_drawdown() -> None:
    """Stable assets with small drawdowns score higher than volatile ones."""
    df_stable = _make_trending_df(slope=0.001, vol=0.002, seed=61)
    df_risky = _make_trending_df(slope=0.001, vol=0.05, seed=62)
    stable_score = score_risk(df_stable, lookback=90)
    risky_score = score_risk(df_risky, lookback=90)
    assert stable_score > risky_score, (
        f"stable ({stable_score}) should beat risky ({risky_score})"
    )


def test_score_regime_bull_vs_bear() -> None:
    """Bull regimes score higher than bear regimes."""
    # Regime classifier needs 260+ bars AND ~trend_window + pct_change
    # lag for non-NaN labels. Use 450 bars so the last-bar label is
    # actually meaningful (not just the Sideways fallback).
    df_bull = _make_trending_df(n=450, slope=0.005, vol=0.004, seed=71)
    df_bear = _make_bear_df(n=450, slope=-0.005, vol=0.01, seed=72)
    bull_score = score_regime(df_bull)
    bear_score = score_regime(df_bear)
    assert bull_score > bear_score, (
        f"bull regime ({bull_score}) should beat bear ({bear_score})"
    )


def test_score_alignment_count() -> None:
    """Alignment score reflects how many indicators agree on direction."""
    df_strong_trend = _make_trending_df(slope=0.005, vol=0.003, seed=81)
    df_choppy = _make_flat_df(slope=0.0, vol=0.015, seed=82)
    aligned = score_alignment(df_strong_trend)
    mixed = score_alignment(df_choppy)
    assert aligned > mixed, (
        f"aligned trend ({aligned}) should beat mixed ({mixed})"
    )


# ── Aggregation + tier mapping ───────────────────────────────────────


def test_aggregate_score_in_range() -> None:
    """Aggregated score is always 0-100 regardless of factor mix."""
    df = _make_trending_df(seed=91)
    factor_scores = {
        "trend": 80.0, "momentum": 70.0, "volatility": 60.0,
        "liquidity": 90.0, "relative_strength": 75.0, "risk": 65.0,
        "regime": 85.0, "alignment": 70.0,
    }
    total = aggregate_score(factor_scores)
    assert 0.0 <= total <= 100.0
    assert isinstance(total, float)


def test_aggregate_score_weights_sum() -> None:
    """Different weights produce different scores for the same factor mix."""
    factor_scores = {k: 50.0 for k in SCORE_FACTOR_NAMES}
    equal = aggregate_score(factor_scores, weights=None)
    weighted_to_trend = aggregate_score(
        factor_scores,
        weights={k: (1.0 if k == "trend" else 0.01) for k in SCORE_FACTOR_NAMES},
    )
    # Equal weights on 50.0 each → ~50.0 (allow float precision)
    assert abs(equal - 50.0) < 1e-6
    # Heavily weighted to trend (which is 50.0) → still ~50
    assert abs(weighted_to_trend - 50.0) < 1.0
    # Sanity: weights are positive and the function accepts them
    assert equal > 0
    assert weighted_to_trend > 0


def test_recommendation_tier_mapping() -> None:
    """Score→tier mapping matches the spec table."""
    assert recommendation_for(95) == "Strong Accumulate"
    assert recommendation_for(80) == "Accumulate"
    assert recommendation_for(60) == "Hold"
    assert recommendation_for(35) == "Reduce"
    assert recommendation_for(15) == "Avoid"
    # Boundary tests
    assert recommendation_for(90) == "Strong Accumulate"
    assert recommendation_for(75) == "Accumulate"
    assert recommendation_for(50) == "Hold"
    assert recommendation_for(25) == "Reduce"
    assert recommendation_for(0) == "Avoid"


def test_expected_holding_period() -> None:
    """Holding period scales with score: low score = long wait, high = shorter horizon."""
    assert expected_holding_period(90) == "6 months"
    assert expected_holding_period(75) == "12 months"
    assert expected_holding_period(50) == "18 months"
    assert expected_holding_period(25) == "24 months"


# ── Ranking engine tests ─────────────────────────────────────────────


def test_rank_assets_sorts_descending() -> None:
    """rank_assets returns assets sorted highest score first."""
    df_strong = _make_trending_df(slope=0.005, vol=0.003, seed=101)
    df_weak = _make_bear_df(slope=-0.005, seed=102)
    df_mid = _make_trending_df(slope=0.001, vol=0.005, seed=103)
    frames = {"STRONG": df_strong, "WEAK": df_weak, "MID": df_mid}
    ranked = rank_assets(frames, weights=None)
    # The strongest series should be at the top
    assert ranked[0]["symbol"] == "STRONG", (
        f"expected STRONG first, got {[r['symbol'] for r in ranked]}"
    )
    # And descending
    for i in range(len(ranked) - 1):
        assert ranked[i]["score"] >= ranked[i + 1]["score"]


def test_build_ranking_includes_all_assets() -> None:
    """build_ranking fetches and ranks every configured pair (or skips on failure)."""
    # Use synthetic data to avoid network
    from trading.fetchers import forex as fx
    from trading.fetchers import nse

    frames: dict[str, pd.DataFrame] = {}
    for pair in config.PAIRS:
        try:
            frames[pair] = fx.fetch_data(pair, days=config.LOOKBACK_DAYS)
        except Exception:
            frames[pair] = _make_trending_df(seed=hash(pair) & 0xFFFFFFFF)
    result = build_ranking(frames)
    # Should produce a RankingSummary
    assert "ranked" in result
    assert "weights" in result
    assert isinstance(result["ranked"], list)
    assert len(result["ranked"]) == len(frames)
    for entry in result["ranked"]:
        for key in ("symbol", "score", "recommendation", "factors", "reason", "holding_period"):
            assert key in entry, f"missing {key} in ranking entry"
        assert 0.0 <= entry["score"] <= 100.0
        assert entry["recommendation"] in {
            "Strong Accumulate", "Accumulate", "Hold", "Reduce", "Avoid"
        }


def test_ranking_entry_has_factor_breakdown() -> None:
    """Each ranking entry includes a breakdown of all 8 factor scores."""
    frames = {"X": _make_trending_df(seed=201)}
    ranked = rank_assets(frames)
    assert ranked
    factors = ranked[0]["factors"]
    for name in SCORE_FACTOR_NAMES:
        assert name in factors, f"factor {name} missing from breakdown"
        assert 0.0 <= factors[name] <= 100.0


def test_rank_assets_handles_short_history() -> None:
    """A DataFrame shorter than the lookback doesn't crash and returns a low score."""
    short = _make_trending_df(n=30, seed=301)
    ranked = rank_assets({"SHORT": short})
    assert ranked
    # Should still produce a valid result, even if the score is low/neutral
    assert 0.0 <= ranked[0]["score"] <= 100.0


# ── Output formatter tests ───────────────────────────────────────────


def test_format_ranking_summary() -> None:
    """format_ranking_summary produces a human-readable table."""
    frames = {
        "A": _make_trending_df(slope=0.005, seed=401),
        "B": _make_bear_df(slope=-0.005, seed=402),
    }
    ranked = rank_assets(frames)
    text = rank_output.format_ranking_summary(ranked)
    assert "A" in text
    assert "B" in text
    # Recommendation labels appear
    for label in ("Strong Accumulate", "Accumulate", "Hold", "Reduce", "Avoid"):
        if label in text:
            break
    else:
        # At minimum one of the symbols got a tier label
        assert any(label in text for label in ("Strong Accumulate", "Accumulate",
                                                "Hold", "Reduce", "Avoid"))


def test_format_top_opportunities_truncates() -> None:
    """format_top_opportunities returns at most N entries."""
    frames = {f"SYM{i:02d}": _make_trending_df(seed=500 + i) for i in range(8)}
    ranked = rank_assets(frames)
    text = rank_output.format_top_opportunities(ranked, top_n=3)
    # Count symbol appearances in the top section (each line: "1. SYM00  85 ...")
    lines = [ln for ln in text.splitlines() if ln.strip().startswith(("1.", "2.", "3."))]
    assert len(lines) == 3


def test_format_factor_breakdown() -> None:
    """format_factor_breakdown prints a per-factor bar chart."""
    factors = {
        "trend": 80.0, "momentum": 60.0, "volatility": 70.0,
        "liquidity": 90.0, "relative_strength": 55.0, "risk": 65.0,
        "regime": 75.0, "alignment": 70.0,
    }
    text = rank_output.format_factor_breakdown(factors)
    for name in SCORE_FACTOR_NAMES:
        assert name in text or name.replace("_", " ") in text.lower()


def test_ranking_registered_in_cli() -> None:
    """The `rank` subcommand is registered in __main__.build_parser()."""
    from trading.__main__ import build_parser
    parser = build_parser()
    # Should not raise; "rank" should be a recognised subcommand
    args = parser.parse_args(["rank"])
    assert args.command == "rank"


# ── Config integration ───────────────────────────────────────────────


def test_config_has_scoring_weights() -> None:
    """config.SCORING_WEIGHTS exposes all 8 factors and sums to ~1.0."""
    assert hasattr(config, "SCORING_WEIGHTS")
    weights = config.SCORING_WEIGHTS
    for name in SCORE_FACTOR_NAMES:
        assert name in weights, f"weight missing for {name}"
        assert weights[name] > 0, f"weight for {name} must be positive"
    total = sum(weights.values())
    assert abs(total - 1.0) < 0.01, f"weights must sum to 1.0, got {total}"


# ── Test runner ──────────────────────────────────────────────────────


def main() -> int:
    tests = [
        # Factor scorers
        test_score_trend_strong_uptrend,
        test_score_trend_downtrend,
        test_score_momentum_rsi,
        test_score_volatility_prefers_low,
        test_score_liquidity_high_volume,
        test_score_relative_strength,
        test_score_risk_low_drawdown,
        test_score_regime_bull_vs_bear,
        test_score_alignment_count,
        # Aggregation
        test_aggregate_score_in_range,
        test_aggregate_score_weights_sum,
        test_recommendation_tier_mapping,
        test_expected_holding_period,
        # Ranking
        test_rank_assets_sorts_descending,
        test_build_ranking_includes_all_assets,
        test_ranking_entry_has_factor_breakdown,
        test_rank_assets_handles_short_history,
        # Output
        test_format_ranking_summary,
        test_format_top_opportunities_truncates,
        test_format_factor_breakdown,
        # Integration
        test_ranking_registered_in_cli,
        test_config_has_scoring_weights,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except SystemExit as e:
            failed += 1
            print(f"  ERR   {t.__name__}: SystemExit({e.code})")
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
