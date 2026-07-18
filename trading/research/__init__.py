"""Research platform — regime analysis, risk metrics, comparison, experiments."""
from .regimes import classify_regimes, compute_regime_breakdown, RegimeBreakdown
from .risk_metrics import compute_expanded_metrics
from .comparison import (
    compare_strategies,
    format_comparison_table,
    format_multi_pair_comparison,
    ComparisonReport,
    ComparisonRow,
)
from .experiments import record_experiment, list_experiments, format_experiment_summary

__all__ = [
    "classify_regimes",
    "compute_regime_breakdown",
    "RegimeBreakdown",
    "compute_expanded_metrics",
    "compare_strategies",
    "format_comparison_table",
    "format_multi_pair_comparison",
    "ComparisonReport",
    "ComparisonRow",
    "record_experiment",
    "list_experiments",
    "format_experiment_summary",
]
