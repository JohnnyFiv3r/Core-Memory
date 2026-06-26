from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.causal.runner import _repo_commit

from .reporting import build_suite_report, render_summary
from .t1 import available_strategies, run_t1_matrix
from .t2 import default_fixture_path, run_t2_calibration


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


def _parse_tasks(raw: str) -> list[str]:
    value = str(raw or "").strip().lower()
    if not value or value == "all":
        return ["t1", "t2"]
    requested = [s.strip() for s in value.split(",") if s.strip()]
    supported = {"t1", "t2"}
    unknown = [s for s in requested if s not in supported]
    if unknown:
        raise ValueError(f"unsupported_task:{','.join(unknown)}")
    return requested


def run_suite(
    *,
    fixtures_dir: Path,
    gold_dir: Path,
    t2_fixture: Path | None = None,
    strategies: list[str] | tuple[str, ...] | None = None,
    tasks: list[str] | tuple[str, ...] | None = None,
    subset: str = "full",
    limit: int | None = None,
) -> dict[str, Any]:
    selected = list(strategies or available_strategies())
    selected_tasks = list(tasks or ["t1", "t2"])
    t1_report: dict[str, Any] = {}
    t2_report: dict[str, Any] | None = None
    if "t1" in selected_tasks:
        t1_report = run_t1_matrix(
            fixtures_dir=fixtures_dir,
            gold_dir=gold_dir,
            strategies=selected,
            subset=subset,
            limit=limit,
        )
    if "t2" in selected_tasks:
        t2_report = run_t2_calibration(fixture_path=t2_fixture or default_fixture_path())
    metadata = {
        "suite": "causal_continuity",
        "task_count": len(selected_tasks),
        "tasks": selected_tasks,
        "strategies": selected,
        "subset": subset,
        "limit": limit,
        "commit": _repo_commit(),
        "notes": [
            "pr1_t1_strategy_matrix",
            "pr2_t2_calibration_reliability",
            "causal_survival_rate_headline",
            "faithfulness_flags_reported",
        ],
    }
    return build_suite_report(metadata=metadata, t1_report=t1_report, t2_report=t2_report)


def main() -> int:
    here = Path(__file__).resolve().parent
    causal_dir = here.parent / "causal"
    p = argparse.ArgumentParser(description="Run the causal-continuity evaluation suite")
    p.add_argument("--fixtures", default=str(causal_dir / "fixtures"))
    p.add_argument("--gold", default=str(causal_dir / "gold"))
    p.add_argument("--t2-fixture", default=str(default_fixture_path()))
    p.add_argument("--tasks", default="all", help="Comma-separated task list, or 'all'. Supported: t1, t2")
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
        t2_fixture=Path(args.t2_fixture),
        strategies=_parse_strategies(str(args.strategies)),
        tasks=_parse_tasks(str(args.tasks)),
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
