"""Market Intelligence layer — news, macro, earnings context for recommendations.

Sits between the Ranking Engine and the Portfolio Advisor. Every recommendation
should explain *why* the market is moving, not just *that* it is.

Public entry points
-------------------

- ``news.fetch(symbols)``        — latest headlines per ticker
- ``calendar.fetch()``            — upcoming macro events
- ``earnings.fetch(symbols)``     — earnings calendar
- ``sector.snapshot()``           — sector rotation
- ``sentiment.score(text)``       — sentiment for one headline
- ``context.assemble(symbol)``    — top-3 context items for one symbol
- ``scanner.run()``               — scheduled orchestrator
- ``cache.get/set``               — JSON file cache
- ``sources.registry``            — source registry + rate limits

All functions degrade gracefully — if a network call fails or no API
keys are configured, the caller still gets a useful empty result.
"""
from __future__ import annotations

# Lazy imports: each submodule is imported on first access via
# ``__getattr__``. This keeps the package importable even while
# individual modules are partially built (handy during TDD).
__all__ = [
    "cache",
    "calendar",
    "context",
    "earnings",
    "news",
    "scanner",
    "sector",
    "sentiment",
    "sources",
]


def __getattr__(name: str):
    import importlib
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
