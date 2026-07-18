"""``trading decision`` — holistic portfolio allocation recommendation.

Outputs a complete strategic allocation (cash, equities, forex, gold)
with a plain-English rationale, instead of per-asset scores in isolation.

Flags
-----
--tilt TEXT           Override the strategy tilt (Defensive/Balanced/Growth)
--no-portfolio        Ignore paper portfolio state (show theoretical allocation)
--verbose             Show per-line reason column
--json                Machine-readable JSON output
"""
from __future__ import annotations

import sys
from typing import Optional

from .. import output
from ...services import decision as decision_svc


def run(
    tilt: Optional[str] = None,
    no_portfolio: bool = False,
    verbose: bool = False,
    as_json: bool = False,
) -> int:
    """Build and display the portfolio allocation recommendation."""
    proposal = decision_svc.build(
        tilt=tilt,
        portfolio_aware=(not no_portfolio),
    )

    if as_json:
        print(output.json_dumps(proposal.to_dict()))
        return 0

    text = decision_svc.format_proposal(proposal, verbose=verbose)
    print(text)
    return 0
