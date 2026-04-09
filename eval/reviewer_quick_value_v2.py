"""Reviewer quick-value path v2 (PV-2)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core_memory.runtime.reviewer_quick_value import reviewer_quick_value_v2


def main() -> int:
    p = argparse.ArgumentParser(description="Run reviewer quick-value v2 walkthrough")
    p.add_argument("--root", default=".")
    p.add_argument("--strict", action="store_true", help="Return non-zero when the full quick-value path does not pass")
    args = p.parse_args()

    out = reviewer_quick_value_v2(Path(args.root))
    print(json.dumps(out, indent=2))

    if not args.strict:
        return 0
    return 0 if bool(((out.get("overall") or {}).get("quick_value_passed"))) else 2


if __name__ == "__main__":
    raise SystemExit(main())
