"""Strategy exports — frozen benchmark + research strategies."""
from .base import BaseStrategy, StrategyMeta
from .sma_crossover import SmaCrossover
from .trend_filter import TrendFilteredSma
from .momentum_trend import MomentumTrend
from .volume_breakout import VolumeBreakout
from .multi_factor import MultiFactor

# Registry of all available strategies for the comparison engine.
# Add new strategies here when they are created.
REGISTRY: dict[str, BaseStrategy] = {
    "A": SmaCrossover(),       # Benchmark — frozen
    "C": TrendFilteredSma(),   # SMA(200) trend filter
    "D": MomentumTrend(),      # ★ PRIMARY — 73% BH capture, 0.83 Sharpe
    "F": MultiFactor(),        # ★ Secondary — 51% BH capture, 0.78 Sharpe
}


def get_strategy(key: str) -> BaseStrategy:
    """Return a strategy by its registry key (``\"A\"``, ``\"C\"``, …)."""
    if key not in REGISTRY:
        raise KeyError(
            f"Unknown strategy '{key}'. Available: {', '.join(sorted(REGISTRY))}"
        )
    return REGISTRY[key]


def list_strategies() -> list[tuple[str, BaseStrategy]]:
    """Return all registered strategies as ``(key, instance)`` pairs."""
    return sorted(REGISTRY.items())
