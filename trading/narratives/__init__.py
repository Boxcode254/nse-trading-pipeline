"""Natural-language narrative generators.

Each module is a pure, side-effect-free function that turns one
piece of structured data (RSI, trend, score, ranking entry, full
universe) into a small dict or string. Templates under
:mod:`trading.templates` consume these to build user-facing
reports.

Public surface
--------------
translator      - per-indicator plain-language translations
risk            - per-asset risk descriptions
confidence      - confidence-level explanations
market_context  - broader market context narrative
"""
from . import translator, risk, confidence, market_context

__all__ = ["translator", "risk", "confidence", "market_context"]
