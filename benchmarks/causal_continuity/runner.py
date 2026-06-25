from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.causal.runner import _repo_commit

from .reporting import build_suite_report, render_summary
from .t1 import available_strategies, run_t1_matrix


def _parse_strategies(raw: str) -> list[str]:
    value = str(raw or "").strip()
    if not value or value == "all":
        return list(available_strategies())
    requested = [s.strip() for s in value.split(",") if s.strip()]
    supported = set(available_strategies())
    unknown = [s for s in requested if s not in supported]
    if unknown:
        raise ValueError(f"unsupported_strategy:{','.join(unknown)}")
    return requested


def run_suite(
    *,
    fixtures_dir: Path,
    gold_dir: Path,
    strategies: list[str] | tuple[str, ...] | None = None,
    subset: str = "full",
    limit: int | None = None,
) -> dict[str, Any]:
    selected = list(strategies or available_strategies())
    t1_report = run_t1_matrix(
        fixtures_dir=fixtures_dir,
        gold_dir=gold_dir,
        strategies=selected,
        subset=subset,
        limit=limit,
    )
    metadata = {
        "suite": "causal_continuity",
        "task_count": 1,
        "tasks": ["t1_causal_chain_reconstruction"],
        "strategies": selected,
        "subset": subset,
        "limit": limit,
        "commit": _repo_commit(),
        "notes": [
            "pr1_t1_strategy_matrix",
            "causal_survival_rate_headline",
            "faithfulness_flags_reported",
        ],
    }
    return build_suite_report(metadata=metadata, t1_report=t1_report)


def main() -> int:
    here = Path(__file__).resolve().parent
    causal_dir = here.parent / "causal"
    p = argparse.ArgumentParser(description="Run the causal-continuity evaluation suite")
    p.add_argument("--fixtures", default=str(causal_dir / "fixtures"))
    p.add_argument("--gold", default=str(causal_dir / "gold"))
    p.add_argument("--subset", choices=["local", "full"], default="full")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--strategies",
        default="all",
        help="Comma-separated strategy list, or 'all'. Supported: " + ", ".join(available_strategies()),
    )
    p.add_argument("--out", default="")
    args = p.parse_args()

    report = run_suite(
        fixtures_dir=Path(args.fixtures),
        gold_dir=Path(args.gold),
        strategies=_parse_strategies(str(args.strategies)),
        subset=str(args.subset),
        limit=args.limit,
    )

    print(render_summary(report))
    print(json.dumps(report, indent=2))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
