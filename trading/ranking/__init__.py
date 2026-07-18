"""Market Ranking Engine.

Turns per-asset OHLCV data into a single 0-100 investment score per
asset, then ranks all configured assets so the system can answer
"Where is the best place to invest today?".

Public surface
--------------
scorer   - 8 factor scorers + weighted aggregation
ranker   - end-to-end ranking + tier + reason generation
output   - plain-language formatter for the CLI / report
"""
from .scorer import (
    SCORE_FACTOR_NAMES,
    score_trend,
    score_momentum,
    score_volatility,
    score_liquidity,
    score_relative_strength,
    score_risk,
    score_regime,
    score_alignment,
    aggregate_score,
)
from .ranker import (
    rank_assets,
    build_ranking,
    recommendation_for,
    expected_holding_period,
)
from . import output

__all__ = [
    "SCORE_FACTOR_NAMES",
    "score_trend",
    "score_momentum",
    "score_volatility",
    "score_liquidity",
    "score_relative_strength",
    "score_risk",
    "score_regime",
    "score_alignment",
    "aggregate_score",
    "rank_assets",
    "build_ranking",
    "recommendation_for",
    "expected_holding_period",
    "output",
]
