"""Dreamer behavior-change eval scaffold (DR-7)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core_memory.runtime.dreamer_eval import dreamer_eval_report


def main() -> int:
    p = argparse.ArgumentParser(description="Run Dreamer behavior eval summary")
    p.add_argument("--root", default=".")
    p.add_argument("--since", default="30d")
    p.add_argument("--strict", action="store_true", help="Return non-zero when core DR-7 metrics are all zero")
    args = p.parse_args()

    out = dreamer_eval_report(Path(args.root), since=str(args.since))
    print(json.dumps(out, indent=2))

    if not args.strict:
        return 0

    m = out.get("metrics") or {}
    core = [
        float(m.get("accepted_candidate_rate") or 0.0),
        float(m.get("cross_session_transfer_success_rate") or 0.0),
        float(m.get("downstream_retrieval_use_rate_of_accepted_outputs") or 0.0),
    ]
    return 0 if any(v > 0.0 for v in core) else 2


if __name__ == "__main__":
    raise SystemExit(main())
