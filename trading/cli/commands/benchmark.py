"""``trading benchmark`` — show benchmark strategy details."""
from __future__ import annotations

from .. import output
from ...services import strategies as strat_svc


def run(quiet: bool = False, as_json: bool = False, verbose: bool = False, output_path: str | None = None) -> int:
    """Show details for the benchmark strategy (Strategy A)."""
    bench = strat_svc.benchmark()
    if as_json:
        print(output.json_dumps(bench))
        return 0
    if quiet:
        print(f"{bench['key']}  {bench['name']}  {bench['status']}")
        return 0
    print(f"\n🏛  Benchmark Strategy: {bench['name']}  ({bench['key']})")
    print(f"   Status:      {bench['status']}  (frozen: {bench.get('frozen', False)})")
    print(f"   Description: {bench['description']}")
    print(f"   Version:     {bench.get('version', '?')}")
    print(f"   Parameters:  {bench.get('params', {})}")
    return 0
