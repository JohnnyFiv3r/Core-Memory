from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.causal.runner import _repo_commit

from .ablations import build_ablation_matrix
from .real_data import build_real_data_contrast
from .reporting import build_evidence_manifest, build_suite_report, render_summary
from .runtime_ablations import run_runtime_ablation_toggles
from .t1 import available_strategies, run_t1_matrix
from .t2 import default_fixture_path, run_t2_calibration
from .t3 import default_fixture_path as default_t3_fixture_path
from .t3 import run_t3_temporal_state
from .t4 import default_fixture_path as default_t4_fixture_path
from .t4 import run_t4_longitudinal_continuity
from .t5 import default_fixture_path as default_t5_fixture_path
from .t5 import run_t5_thread_fidelity


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
        return ["t1", "t2", "t3", "t4", "t5"]
    requested = [s.strip() for s in value.split(",") if s.strip()]
    supported = {"t1", "t2", "t3", "t4", "t5"}
    unknown = [s for s in requested if s not in supported]
    if unknown:
        raise ValueError(f"unsupported_task:{','.join(unknown)}")
    return requested


def run_suite(
    *,
    fixtures_dir: Path,
    gold_dir: Path,
    t2_fixture: Path | None = None,
    t3_fixture: Path | None = None,
    t4_fixture: Path | None = None,
    t5_fixture: Path | None = None,
    strategies: list[str] | tuple[str, ...] | None = None,
    tasks: list[str] | tuple[str, ...] | None = None,
    subset: str = "full",
    limit: int | None = None,
    include_ablations: bool = False,
    run_ablation_toggles: bool = False,
    include_real_data_contrast: bool = False,
    run_real_data_local_proxy: bool = False,
    real_data_local_limit: int = 1,
    locomo_corpus: Path | None = None,
    longmemeval_corpus: Path | None = None,
    run_real_data_adapter_smoke: bool = False,
    real_data_adapter_limit: int = 1,
    run_real_data_eval_smoke: bool = False,
    real_data_eval_limit: int = 1,
    external_memory_adapter: str = "",
    external_memory_command: str = "",
    long_context_adapter: str = "",
    long_context_command: str = "",
    t5_judge: str = "deterministic",
    evidence_attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = list(strategies or available_strategies())
    selected_tasks = list(tasks or ["t1", "t2", "t3", "t4", "t5"])
    t1_report: dict[str, Any] = {}
    t2_report: dict[str, Any] | None = None
    t3_report: dict[str, Any] | None = None
    t4_report: dict[str, Any] | None = None
    t5_report: dict[str, Any] | None = None
    if "t1" in selected_tasks:
        t1_report = run_t1_matrix(
            fixtures_dir=fixtures_dir,
            gold_dir=gold_dir,
            strategies=selected,
            subset=subset,
            limit=limit,
            external_memory_adapter=external_memory_adapter,
            external_memory_command=external_memory_command,
            long_context_adapter=long_context_adapter,
            long_context_command=long_context_command,
        )
    if "t2" in selected_tasks:
        t2_report = run_t2_calibration(fixture_path=t2_fixture or default_fixture_path())
    if "t3" in selected_tasks:
        t3_report = run_t3_temporal_state(fixture_path=t3_fixture or default_t3_fixture_path())
    if "t4" in selected_tasks:
        t4_report = run_t4_longitudinal_continuity(fixture_path=t4_fixture or default_t4_fixture_path())
    if "t5" in selected_tasks:
        t5_report = run_t5_thread_fidelity(
            fixture_path=t5_fixture or default_t5_fixture_path(),
            judge_kind=t5_judge,
        )
    notes = [
        "pr1_t1_strategy_matrix",
        "pr2_t2_calibration_reliability",
        "pr3_t3_temporal_state_selection",
        "pr4_t4_longitudinal_continuity",
        "pr5_t5_thread_fidelity",
        "pr6_ablation_matrix",
        "pr8_baseline_completion",
        "causal_survival_rate_headline",
        "faithfulness_flags_reported",
    ]
    if include_real_data_contrast:
        notes.append("pr7_real_data_contrast")
    if run_ablation_toggles:
        notes.append("pr9_runtime_ablation_toggles")

    metadata = {
        "suite": "causal_continuity",
        "task_count": len(selected_tasks),
        "tasks": selected_tasks,
        "strategies": selected,
        "subset": subset,
        "limit": limit,
        "commit": _repo_commit(),
        "notes": notes,
    }
    report = build_suite_report(
        metadata=metadata,
        t1_report=t1_report,
        t2_report=t2_report,
        t3_report=t3_report,
        t4_report=t4_report,
        t5_report=t5_report,
    )
    if evidence_attestation:
        report["evidence_attestation"] = dict(evidence_attestation)
    if run_ablation_toggles:
        runtime_runs = run_runtime_ablation_toggles(
            report,
            t2_fixture=t2_fixture or default_fixture_path(),
            t3_fixture=t3_fixture or default_t3_fixture_path(),
            t5_fixture=t5_fixture or default_t5_fixture_path(),
        )
        report["ablation_matrix"] = build_ablation_matrix(report, runtime_runs=runtime_runs)
    elif include_ablations:
        report["ablation_matrix"] = build_ablation_matrix(report)
    if include_real_data_contrast:
        report["real_data_contrast"] = build_real_data_contrast(
            locomo_corpus=locomo_corpus,
            longmemeval_corpus=longmemeval_corpus,
            run_local_proxy=run_real_data_local_proxy,
            local_proxy_limit=real_data_local_limit,
            run_external_adapter_smoke=run_real_data_adapter_smoke,
            external_adapter_limit=real_data_adapter_limit,
            run_external_eval_smoke=run_real_data_eval_smoke,
            external_eval_limit=real_data_eval_limit,
        )
    report["evidence_manifest"] = build_evidence_manifest(report)
    return report


def main() -> int:
    here = Path(__file__).resolve().parent
    causal_dir = here.parent / "causal"
    p = argparse.ArgumentParser(description="Run the causal-continuity evaluation suite")
    p.add_argument("--fixtures", default=str(causal_dir / "fixtures"))
    p.add_argument("--gold", default=str(causal_dir / "gold"))
    p.add_argument("--t2-fixture", default=str(default_fixture_path()))
    p.add_argument("--t3-fixture", default=str(default_t3_fixture_path()))
    p.add_argument("--t4-fixture", default=str(default_t4_fixture_path()))
    p.add_argument("--t5-fixture", default=str(default_t5_fixture_path()))
    p.add_argument("--tasks", default="all", help="Comma-separated task list, or 'all'. Supported: t1, t2, t3, t4, t5")
    p.add_argument("--include-ablations", action="store_true", help="Attach the PRD section 7 ablation matrix to the suite report")
    p.add_argument("--run-ablation-toggles", action="store_true", help="Execute supported disabled-mode ablation runs and attach the runtime ablation matrix")
    p.add_argument("--include-real-data-contrast", action="store_true", help="Attach real-data contrast readiness without making leaderboard claims")
    p.add_argument("--run-real-data-local-proxy", action="store_true", help="Run the checked-in LOCOMO-like local proxy inside the real-data contrast attachment")
    p.add_argument("--run-real-data-adapter-smoke", action="store_true", help="Load-smoke supplied external corpora inside the real-data contrast attachment")
    p.add_argument("--run-real-data-eval-smoke", action="store_true", help="Run bounded lifecycle evaluation smoke for supplied external corpora")
    p.add_argument("--real-data-local-limit", type=int, default=1, help="Case limit for --run-real-data-local-proxy")
    p.add_argument("--real-data-adapter-limit", type=int, default=1, help="Corpus instance limit for --run-real-data-adapter-smoke")
    p.add_argument("--real-data-eval-limit", type=int, default=1, help="Conversation/instance limit for --run-real-data-eval-smoke")
    p.add_argument("--locomo-corpus", default="", help="Optional path to user-supplied locomo10.json for external LoCoMo adapter readiness checks")
    p.add_argument("--longmemeval-corpus", default="", help="Optional path to user-supplied LongMemEval JSON/JSONL corpus for adapter readiness checks")
    p.add_argument("--external-memory-adapter", default="", help="Optional external-memory T1 adapter name; 'fake' exercises the offline adapter contract, 'command' uses --external-memory-command")
    p.add_argument("--external-memory-command", default="", help="Optional command for the T1 external-memory adapter protocol")
    p.add_argument("--long-context-adapter", default="", help="Optional long-context T1 adapter name; 'command' uses --long-context-command")
    p.add_argument("--long-context-command", default="", help="Optional command for the T1 long-context/no-memory adapter protocol")
    p.add_argument("--t5-judge", default="deterministic", help="T5 answerability judge kind: deterministic, fake_llm, or llm")
    p.add_argument("--evidence-attestation", default="", help="Optional causal_continuity.evidence_attestation.v1 JSON file for documented external claim gates")
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
        t3_fixture=Path(args.t3_fixture),
        t4_fixture=Path(args.t4_fixture),
        t5_fixture=Path(args.t5_fixture),
        strategies=_parse_strategies(str(args.strategies)),
        tasks=_parse_tasks(str(args.tasks)),
        subset=str(args.subset),
        limit=args.limit,
        include_ablations=bool(args.include_ablations),
        run_ablation_toggles=bool(args.run_ablation_toggles),
        include_real_data_contrast=bool(args.include_real_data_contrast),
        run_real_data_local_proxy=bool(args.run_real_data_local_proxy),
        real_data_local_limit=int(args.real_data_local_limit),
        run_real_data_adapter_smoke=bool(args.run_real_data_adapter_smoke),
        real_data_adapter_limit=int(args.real_data_adapter_limit),
        run_real_data_eval_smoke=bool(args.run_real_data_eval_smoke),
        real_data_eval_limit=int(args.real_data_eval_limit),
        external_memory_adapter=str(args.external_memory_adapter or ""),
        external_memory_command=str(args.external_memory_command or ""),
        long_context_adapter=str(args.long_context_adapter or ""),
        long_context_command=str(args.long_context_command or ""),
        t5_judge=str(args.t5_judge or "deterministic"),
        evidence_attestation=(
            json.loads(Path(args.evidence_attestation).read_text(encoding="utf-8"))
            if str(args.evidence_attestation or "").strip()
            else None
        ),
        locomo_corpus=(Path(args.locomo_corpus) if str(args.locomo_corpus or "").strip() else None),
        longmemeval_corpus=(Path(args.longmemeval_corpus) if str(args.longmemeval_corpus or "").strip() else None),
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
