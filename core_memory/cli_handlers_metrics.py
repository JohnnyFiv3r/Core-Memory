from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .persistence.archive_index import rebuild_archive_index
from .runtime.dreamer_eval import dreamer_eval_report
from .runtime.longitudinal_benchmark import longitudinal_benchmark_v2
from .runtime.reviewer_quick_value import reviewer_quick_value_v2
from .runtime.myelination import myelination_report


def handle_metrics_command(*, args: Any, memory: Any, metrics_parser: Any, canonical_health_report: Callable[[str, str | None], dict]) -> bool:
    """Handle `core-memory metrics ...` commands.

    Returns True when handled (including help output), else False.
    """
    if getattr(args, "command", None) != "metrics":
        return False

    if args.metrics_cmd == "report":
        print(json.dumps(memory.metrics_report(since=args.since), indent=2))
    elif args.metrics_cmd == "schema-quality":
        print(json.dumps(memory.schema_quality_report(write_path=args.write), indent=2))
    elif args.metrics_cmd == "rebalance-promotions":
        print(json.dumps(memory.rebalance_promotions(apply=args.apply), indent=2))
    elif args.metrics_cmd == "promotion-slate":
        print(json.dumps(memory.promotion_slate(limit=args.limit, query_text=args.query), indent=2))
    elif args.metrics_cmd == "decide-promotion":
        print(
            json.dumps(
                memory.decide_promotion(
                    bead_id=args.id,
                    decision=args.decision,
                    reason=args.reason,
                    considerations=args.consideration or [],
                ),
                indent=2,
            )
        )
    elif args.metrics_cmd == "decide-promotion-bulk":
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit("--file must contain a JSON array")
        print(json.dumps(memory.decide_promotion_bulk(payload), indent=2))
    elif args.metrics_cmd == "promotion-kpis":
        print(json.dumps(memory.promotion_kpis(limit=args.limit), indent=2))
    elif args.metrics_cmd == "archive-index-rebuild":
        print(json.dumps(rebuild_archive_index(memory.root), indent=2))
    elif args.metrics_cmd == "start-run":
        print(json.dumps(memory.start_task_run(args.run_id, args.task_id, mode=args.mode, phase=args.phase), indent=2))
    elif args.metrics_cmd == "step":
        print(json.dumps(memory.track_step(args.count), indent=2))
    elif args.metrics_cmd == "tool":
        print(json.dumps(memory.track_tool_call(args.count), indent=2))
    elif args.metrics_cmd == "turn":
        print(json.dumps(memory.track_turn_processed(args.count), indent=2))
    elif args.metrics_cmd == "bead":
        cur = memory.current_run_metrics()
        if args.created:
            cur = memory.track_bead_created(args.created)
        if args.recalled:
            cur = memory.track_bead_recalled(args.recalled)
        print(json.dumps(cur, indent=2))
    elif args.metrics_cmd == "finalize-run":
        print(json.dumps(memory.finalize_task_run(result=args.result), indent=2))
    elif args.metrics_cmd == "recall-eval":
        result = memory.evaluate_rationale_recall(args.question, args.answer, bead_id=args.bead_id)
        if not args.no_log:
            memory.append_metric(
                {
                    "task_id": "rationale_recall",
                    "result": "success" if result.get("score", 0) > 0 else "fail",
                    "rationale_recall_score": result.get("score", 0),
                }
            )
        print(json.dumps(result, indent=2))
    elif args.metrics_cmd == "log":
        rec = memory.append_metric(
            {
                "run_id": args.run_id,
                "mode": args.mode,
                "task_id": args.task_id,
                "result": args.result,
                "steps": args.steps,
                "tool_calls": args.tool_calls,
                "beads_created": args.beads_created,
                "beads_recalled": args.beads_recalled,
                "repeat_failure": args.repeat_failure,
                "decision_conflicts": args.decision_conflicts,
                "unjustified_flips": args.unjustified_flips,
                "rationale_recall_score": args.rationale_recall_score,
                "turns_processed": args.turns_processed,
                "compression_ratio": args.compression_ratio,
                "phase": args.phase,
            }
        )
        print(json.dumps(rec, indent=2))
    elif args.metrics_cmd == "autonomy-log":
        rec = memory.append_autonomy_kpi(
            run_id=args.run_id,
            repeat_failure=args.repeat_failure,
            contradiction_resolved=args.contradiction_resolved,
            contradiction_latency_turns=args.contradiction_latency_turns,
            unjustified_flip=args.unjustified_flip,
            constraint_violation=args.constraint_violation,
            wrong_transfer=args.wrong_transfer,
            goal_carryover=args.goal_carryover,
        )
        print(json.dumps(rec, indent=2))
    elif args.metrics_cmd == "autonomy-report":
        print(json.dumps(memory.autonomy_report(since=args.since), indent=2))
    elif args.metrics_cmd == "canonical-health":
        print(json.dumps(canonical_health_report(str(memory.root), write_path=args.write), indent=2))
    elif args.metrics_cmd == "dreamer-eval":
        out = dreamer_eval_report(memory.root, since=args.since)
        print(json.dumps(out, indent=2))
        if args.write:
            Path(args.write).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        if args.strict:
            m = out.get("metrics") or {}
            core = [
                float(m.get("accepted_candidate_rate") or 0.0),
                float(m.get("cross_session_transfer_success_rate") or 0.0),
                float(m.get("downstream_retrieval_use_rate_of_accepted_outputs") or 0.0),
            ]
            if not any(v > 0.0 for v in core):
                raise SystemExit(2)
    elif args.metrics_cmd == "longitudinal-benchmark-v2":
        out = longitudinal_benchmark_v2(memory.root, since=args.since)
        print(json.dumps(out, indent=2))
        if args.write:
            Path(args.write).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        if args.strict:
            comp = out.get("comparisons") or {}
            if float(comp.get("core_with_dreamer_vs_no_memory_lift") or 0.0) <= 0.0:
                raise SystemExit(2)
    elif args.metrics_cmd == "reviewer-quick-value-v2":
        out = reviewer_quick_value_v2(memory.root)
        print(json.dumps(out, indent=2))
        if args.write:
            Path(args.write).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        if args.strict and not bool((out.get("overall") or {}).get("quick_value_passed")):
            raise SystemExit(2)
    elif args.metrics_cmd == "myelination-experiment":
        out = myelination_report(memory.root, since=args.since, limit=args.limit, top=args.top)
        print(json.dumps(out, indent=2))
        if args.write:
            Path(args.write).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        if args.strict and bool(out.get("enabled")):
            stats = dict(out.get("stats") or {})
            if int(stats.get("beads") or 0) <= 0:
                raise SystemExit(2)
    else:
        metrics_parser.print_help()

    return True
