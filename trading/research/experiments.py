"""Experiment documentation — records every strategy test so we
build knowledge rather than accumulate indicators.

Each experiment records:
1. What was tested?
2. Why was it tested? (hypothesis)
3. What happened? (result)
4. What evidence supports the conclusion? (data)
5. What should be tested next? (next step)

Experiments are stored as JSON in ``~/.trading/research/experiments/``
so they accumulate over time.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from .. import config

EXPERIMENTS_DIR = os.path.join(config.HOME, "research", "experiments")


def _ensure_dir() -> None:
    os.makedirs(EXPERIMENTS_DIR, exist_ok=True)


def record_experiment(
    title: str,
    hypothesis: str,
    strategy_name: str,
    pairs_tested: list[str],
    results_summary: str,
    conclusion: str,
    next_step: str,
    metrics: Optional[dict[str, Any]] = None,
) -> str:
    """Record a completed experiment.

    Returns the experiment ID (timestamp-based).
    """
    _ensure_dir()
    exp_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    record = {
        "id": exp_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "hypothesis": hypothesis,
        "strategy_name": strategy_name,
        "pairs_tested": pairs_tested,
        "results_summary": results_summary,
        "conclusion": conclusion,
        "next_step": next_step,
        "metrics": metrics or {},
    }

    path = os.path.join(EXPERIMENTS_DIR, f"{exp_id}.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2, default=str)

    return exp_id


def list_experiments(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent *limit* experiments."""
    _ensure_dir()
    try:
        files = sorted(
            (f for f in os.listdir(EXPERIMENTS_DIR) if f.endswith(".json")),
            reverse=True,
        )
    except FileNotFoundError:
        return []

    experiments = []
    for fname in files[:limit]:
        path = os.path.join(EXPERIMENTS_DIR, fname)
        try:
            with open(path) as f:
                experiments.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return experiments


def format_experiment_summary(experiments: list[dict[str, Any]]) -> str:
    """Format experiment history as a readable summary."""
    if not experiments:
        return "No experiments recorded yet."

    lines = ["🧪 *EXPERIMENT LOG*", "Recent strategy tests and findings:", ""]
    for exp in experiments:
        lines.append(f"**{exp.get('title', 'Untitled')}**")
        lines.append(f"  Hypothesis: {exp.get('hypothesis', 'N/A')}")
        lines.append(f"  Strategy: {exp.get('strategy_name', 'N/A')}")
        lines.append(f"  Result: {exp.get('results_summary', 'N/A')}")
        lines.append(f"  Conclusion: {exp.get('conclusion', 'N/A')}")
        lines.append(f"  Next: {exp.get('next_step', 'N/A')}")
        lines.append("")
    return "\n".join(lines)
