"""Longitudinal benchmark v2 scaffold (PV-1)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core_memory.runtime.longitudinal_benchmark import longitudinal_benchmark_v2


def main() -> int:
    p = argparse.ArgumentParser(description="Run longitudinal benchmark v2")
    p.add_argument("--root", default=".")
    p.add_argument("--since", default="30d")
    p.add_argument("--strict", action="store_true", help="Return non-zero when dreamer cohort does not beat no-memory baseline")
    args = p.parse_args()

    out = longitudinal_benchmark_v2(Path(args.root), since=str(args.since))
    print(json.dumps(out, indent=2))

    if not args.strict:
        return 0

    comp = out.get("comparisons") or {}
    lift = float(comp.get("core_with_dreamer_vs_no_memory_lift") or 0.0)
    return 0 if lift > 0.0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
