"""Translate a confidence score (0-100) into a plain-English sentence.

Confidence here is the *signal confidence* the validator produces —
not the ranking score. The two are independent: a high-confidence
signal can land on a low-conviction asset, and vice versa.
"""
from __future__ import annotations

from typing import Optional


def explain_confidence(confidence: Optional[float]) -> str:
    """Return a single sentence explaining the confidence level.

    Buckets from the spec:
        75-100 High
        50-74  Medium
        0-49   Low
    """
    if confidence is None:
        return (
            "Confidence could not be computed — treat any recommendation as "
            "tentative until the underlying data stabilises."
        )

    c = float(confidence)
    if c >= 75:
        return (
            "We are highly confident in this recommendation. Multiple "
            "independent indicators are aligned."
        )
    if c >= 50:
        return (
            "There is reasonable evidence supporting this recommendation, "
            "but some indicators are mixed — proceed with normal caution."
        )
    return (
        "Confidence is limited. The signals are weak or contradictory — "
        "this recommendation should be treated as tentative."
    )
