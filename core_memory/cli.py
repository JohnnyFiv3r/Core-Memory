"""
Core-Memory CLI.

Canonical command-line interface for core-memory.
Entry point only - does not export from __init__.py to avoid circular imports.
"""

import argparse
import json
from pathlib import Path

# Use relative import to avoid circular import
from .store import MemoryStore, DEFAULT_ROOT
from .openclaw_integration import (
    coordinator_finalize_hook,
    finalize_and_process_turn,
    process_pending_memory_events,
)


def main():
    """CLI entry point for core-memory command."""
    parser = argparse.ArgumentParser(description="Core-Memory CLI")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Memory root directory")
    
    subparsers = parser.add_subparsers(dest="command")
    
    # add command
    add_parser = subparsers.add_parser("add", help="Add a bead")
    add_parser.add_argument("--type", required=True, help="Bead type")
    add_parser.add_argument("--title", required=True, help="Bead title")
    add_parser.add_argument("--summary", nargs="*", help="Summary points")
    add_parser.add_argument("--because", nargs="*", help="Structured rationale points")
    add_parser.add_argument("--source-turn-ids", nargs="*", help="Provenance turn IDs")
    add_parser.add_argument("--tags", nargs="*", help="Tags")
    add_parser.add_argument("--context-tags", nargs="*", help="Environment/context tags")
    add_parser.add_argument("--session-id", help="Session ID")
    
    # query command
    query_parser = subparsers.add_parser("query", help="Query beads")
    query_parser.add_argument("--type", help="Filter by type")
    query_parser.add_argument("--status", help="Filter by status")
    query_parser.add_argument("--tags", nargs="*", help="Filter by tags")
    query_parser.add_argument("--limit", type=int, default=20)
    
    # stats command
    subparsers.add_parser("stats", help="Show statistics")

    # heads command
    heads_parser = subparsers.add_parser("heads", help="Show topic/goal HEAD pointers")
    heads_parser.add_argument("--topic-id", help="Lookup specific topic HEAD")
    heads_parser.add_argument("--goal-id", help="Lookup specific goal HEAD")

    # preflight failure check (warn-only)
    preflight_parser = subparsers.add_parser("preflight", help="Warn-only failure-signature preflight check")
    preflight_parser.add_argument("--plan", required=True, help="Normalized plan text to check")
    preflight_parser.add_argument("--context-tags", nargs="*", help="Optional environment/context tags")
    preflight_parser.add_argument("--limit", type=int, default=5)

    # phase-3 advisory constraints
    constraints_parser = subparsers.add_parser("constraints", help="List active extracted constraints")
    constraints_parser.add_argument("--limit", type=int, default=20)

    check_plan_parser = subparsers.add_parser("check-plan", help="Advisory constraint compliance check")
    check_plan_parser.add_argument("--plan", required=True)
    check_plan_parser.add_argument("--limit", type=int, default=20)

    # phase-4 environment scoped retrieval
    retrieve_ctx_parser = subparsers.add_parser("retrieve-context", help="Retrieve beads with context tag matching and fallback")
    retrieve_ctx_parser.add_argument("--query", default="")
    retrieve_ctx_parser.add_argument("--context-tags", nargs="*", help="Requested environment tags")
    retrieve_ctx_parser.add_argument("--limit", type=int, default=20)
    retrieve_ctx_parser.add_argument("--no-strict-first", action="store_true")
    
    # dream command
    dream_parser = subparsers.add_parser("dream", help="Run Dreamer analysis")
    dream_parser.add_argument("--novel-only", action="store_true", help="Exclude previously surfaced bead pairs")
    dream_parser.add_argument("--seen-window-runs", type=int, default=0, help="Only consider the last N Dreamer runs for novelty dedupe (0=all)")
    dream_parser.add_argument("--max-exposure", type=int, default=-1, help="Skip candidates where either bead has been surfaced more than this count (-1=disabled)")
    
    # rebuild command
    subparsers.add_parser("rebuild", help="Rebuild index from events")

    # compact command
    compact_parser = subparsers.add_parser("compact", help="Compact beads")
    compact_parser.add_argument("--session", help="Compact only this session")
    compact_parser.add_argument("--promote", action="store_true", help="Promote compacted beads")

    # uncompact command
    uncompact_parser = subparsers.add_parser("uncompact", help="Restore compacted bead detail")
    uncompact_parser.add_argument("--id", required=True, help="Bead ID")

    # myelinate command
    myelinate_parser = subparsers.add_parser("myelinate", help="Run myelination analysis")
    myelinate_parser.add_argument("--apply", action="store_true", help="Apply changes (default dry-run)")

    # migrate-store command
    migrate_parser = subparsers.add_parser("migrate-store", help="Migrate legacy mem_beads store")
    migrate_parser.add_argument("--legacy-root", required=True, help="Path to legacy .mem-beads store")
    migrate_parser.add_argument("--no-backup", action="store_true", help="Disable backup before import")

    # sidecar integration command
    sidecar_parser = subparsers.add_parser("sidecar", help="Coordinator integration helpers")
    sidecar_sub = sidecar_parser.add_subparsers(dest="sidecar_cmd")

    sc_finalize = sidecar_sub.add_parser("finalize", help="Emit finalize memory event (coordinator shim)")
    sc_finalize.add_argument("--session-id", required=True)
    sc_finalize.add_argument("--turn-id", required=True)
    sc_finalize.add_argument("--transaction-id", required=True)
    sc_finalize.add_argument("--trace-id", required=True)
    sc_finalize.add_argument("--user-query", required=True)
    sc_finalize.add_argument("--assistant-final", required=True)
    sc_finalize.add_argument("--trace-depth", type=int, default=0)
    sc_finalize.add_argument("--origin", default="USER_TURN")

    sc_process = sidecar_sub.add_parser("process", help="Process queued memory events")
    sc_process.add_argument("--max-events", type=int, default=50)

    sc_turn = sidecar_sub.add_parser("turn", help="Atomically finalize and process one turn")
    sc_turn.add_argument("--session-id", required=True)
    sc_turn.add_argument("--turn-id", required=True)
    sc_turn.add_argument("--transaction-id", required=True)
    sc_turn.add_argument("--trace-id", required=True)
    sc_turn.add_argument("--user-query", required=True)
    sc_turn.add_argument("--assistant-final", required=True)
    sc_turn.add_argument("--trace-depth", type=int, default=0)
    sc_turn.add_argument("--origin", default="USER_TURN")
    sc_turn.add_argument("--meta-constraint-violation", action="store_true")
    sc_turn.add_argument("--meta-wrong-transfer", action="store_true")
    sc_turn.add_argument("--meta-goal-carryover", action="store_true")
    sc_turn.add_argument("--store-full-text", choices=["true", "false"], default="true")

    # metrics command
    metrics_parser = subparsers.add_parser("metrics", help="Metrics tools")
    metrics_sub = metrics_parser.add_subparsers(dest="metrics_cmd")

    metrics_report = metrics_sub.add_parser("report", help="Aggregate metrics.jsonl deterministically")
    metrics_report.add_argument("--since", default="7d", help="Window, e.g. 7d or 48h")

    metrics_start = metrics_sub.add_parser("start-run", help="Start/reset aggregated run counters")
    metrics_start.add_argument("--run-id", required=True)
    metrics_start.add_argument("--task-id", required=True)
    metrics_start.add_argument("--mode", default="core_memory")
    metrics_start.add_argument("--phase", default="core_memory")

    metrics_step = metrics_sub.add_parser("step", help="Increment step counter for current run")
    metrics_step.add_argument("--count", type=int, default=1)

    metrics_tool = metrics_sub.add_parser("tool", help="Increment tool-call counter for current run")
    metrics_tool.add_argument("--count", type=int, default=1)

    metrics_turn = metrics_sub.add_parser("turn", help="Increment turns-processed counter for current run")
    metrics_turn.add_argument("--count", type=int, default=1)

    metrics_bead = metrics_sub.add_parser("bead", help="Increment bead counters for current run")
    metrics_bead.add_argument("--created", type=int, default=0)
    metrics_bead.add_argument("--recalled", type=int, default=0)

    metrics_finalize = metrics_sub.add_parser("finalize-run", help="Append final KPI row with derived compression ratio")
    metrics_finalize.add_argument("--result", default="success")

    metrics_recall = metrics_sub.add_parser("recall-eval", help="Score rationale recall (0/1/2) deterministically")
    metrics_recall.add_argument("--question", required=True)
    metrics_recall.add_argument("--answer", required=True)
    metrics_recall.add_argument("--bead-id")
    metrics_recall.add_argument("--no-log", action="store_true")

    metrics_log = metrics_sub.add_parser("log", help="Append one metrics record")
    metrics_log.add_argument("--run-id", required=True)
    metrics_log.add_argument("--mode", default="core_memory")
    metrics_log.add_argument("--task-id", required=True)
    metrics_log.add_argument("--result", default="success")
    metrics_log.add_argument("--steps", type=int, default=0)
    metrics_log.add_argument("--tool-calls", type=int, default=0)
    metrics_log.add_argument("--beads-created", type=int, default=0)
    metrics_log.add_argument("--beads-recalled", type=int, default=0)
    metrics_log.add_argument("--repeat-failure", action="store_true")
    metrics_log.add_argument("--decision-conflicts", type=int, default=0)
    metrics_log.add_argument("--unjustified-flips", type=int, default=0)
    metrics_log.add_argument("--rationale-recall-score", type=int, default=0)
    metrics_log.add_argument("--turns-processed", type=int, default=0)
    metrics_log.add_argument("--compression-ratio", type=float, default=0.0)
    metrics_log.add_argument("--phase", default="core_memory")

    metrics_auto = metrics_sub.add_parser("autonomy-log", help="Append one autonomy KPI record")
    metrics_auto.add_argument("--run-id", required=True)
    metrics_auto.add_argument("--repeat-failure", action="store_true")
    metrics_auto.add_argument("--contradiction-resolved", action="store_true")
    metrics_auto.add_argument("--contradiction-latency-turns", type=int, default=0)
    metrics_auto.add_argument("--unjustified-flip", action="store_true")
    metrics_auto.add_argument("--constraint-violation", action="store_true")
    metrics_auto.add_argument("--wrong-transfer", action="store_true")
    metrics_auto.add_argument("--goal-carryover", action="store_true")

    metrics_auto_report = metrics_sub.add_parser("autonomy-report", help="Aggregate autonomy KPIs")
    metrics_auto_report.add_argument("--since", default="7d")
    
    args = parser.parse_args()
    
    memory = MemoryStore(root=args.root)
    
    if args.command == "add":
        bead_id = memory.add_bead(
            type=args.type,
            title=args.title,
            summary=args.summary,
            because=args.because,
            source_turn_ids=args.source_turn_ids,
            tags=args.tags,
            context_tags=args.context_tags,
            session_id=args.session_id
        )
        print(f"Created bead: {bead_id}")
    
    elif args.command == "query":
        results = memory.query(
            type=args.type,
            status=args.status,
            tags=args.tags,
            limit=args.limit
        )
        for bead in results:
            print(f"{bead['id']}: [{bead['type']}] {bead['title']}")
    
    elif args.command == "stats":
        stats = memory.stats()
        print(json.dumps(stats, indent=2))

    elif args.command == "heads":
        heads = memory._read_heads()
        if args.topic_id:
            print(json.dumps({"topic_id": args.topic_id, "head": (heads.get("topics") or {}).get(args.topic_id)}, indent=2))
        elif args.goal_id:
            print(json.dumps({"goal_id": args.goal_id, "head": (heads.get("goals") or {}).get(args.goal_id)}, indent=2))
        else:
            print(json.dumps(heads, indent=2))

    elif args.command == "preflight":
        result = memory.preflight_failure_check(
            plan=args.plan,
            limit=args.limit,
            context_tags=args.context_tags,
        )
        print(json.dumps(result, indent=2))

    elif args.command == "constraints":
        print(json.dumps({"ok": True, "constraints": memory.active_constraints(limit=args.limit)}, indent=2))

    elif args.command == "check-plan":
        result = memory.check_plan_constraints(plan=args.plan, limit=args.limit)
        print(json.dumps(result, indent=2))

    elif args.command == "retrieve-context":
        result = memory.retrieve_with_context(
            query_text=args.query,
            context_tags=args.context_tags,
            limit=args.limit,
            strict_first=not args.no_strict_first,
        )
        print(json.dumps(result, indent=2))
    
    elif args.command == "dream":
        results = memory.dream(
            novel_only=args.novel_only,
            seen_window_runs=args.seen_window_runs,
            max_exposure=args.max_exposure,
        )
        print(json.dumps(results, indent=2))
    
    elif args.command == "rebuild":
        index = memory.rebuild_index()
        print(f"Rebuilt index with {index['stats']['total_beads']} beads")

    elif args.command == "compact":
        result = memory.compact(session_id=args.session, promote=args.promote)
        print(json.dumps(result))

    elif args.command == "uncompact":
        result = memory.uncompact(args.id)
        print(json.dumps(result))
        if not result.get("ok"):
            raise SystemExit(1)

    elif args.command == "myelinate":
        result = memory.myelinate(apply=args.apply)
        print(json.dumps(result))

    elif args.command == "migrate-store":
        result = memory.migrate_legacy_store(args.legacy_root, backup=not args.no_backup)
        print(json.dumps(result, indent=2))

    elif args.command == "sidecar":
        if args.sidecar_cmd == "finalize":
            result = coordinator_finalize_hook(
                root=args.root,
                session_id=args.session_id,
                turn_id=args.turn_id,
                transaction_id=args.transaction_id,
                trace_id=args.trace_id,
                user_query=args.user_query,
                assistant_final=args.assistant_final,
                trace_depth=args.trace_depth,
                origin=args.origin,
            )
            print(json.dumps(result, indent=2))
        elif args.sidecar_cmd == "process":
            result = process_pending_memory_events(args.root, max_events=args.max_events)
            print(json.dumps(result, indent=2))
        elif args.sidecar_cmd == "turn":
            metadata = {
                "constraint_violation": bool(args.meta_constraint_violation),
                "wrong_transfer": bool(args.meta_wrong_transfer),
                "goal_carryover": bool(args.meta_goal_carryover),
                "store_full_text": (args.store_full_text == "true"),
            }
            result = finalize_and_process_turn(
                root=args.root,
                session_id=args.session_id,
                turn_id=args.turn_id,
                transaction_id=args.transaction_id,
                trace_id=args.trace_id,
                user_query=args.user_query,
                assistant_final=args.assistant_final,
                trace_depth=args.trace_depth,
                origin=args.origin,
                metadata=metadata,
            )
            print(json.dumps(result, indent=2))
        else:
            sidecar_parser.print_help()

    elif args.command == "metrics":
        if args.metrics_cmd == "report":
            print(json.dumps(memory.metrics_report(since=args.since), indent=2))
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
                memory.append_metric({
                    "task_id": "rationale_recall",
                    "result": "success" if result.get("score", 0) > 0 else "fail",
                    "rationale_recall_score": result.get("score", 0),
                })
            print(json.dumps(result, indent=2))
        elif args.metrics_cmd == "log":
            rec = memory.append_metric({
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
            })
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
        else:
            metrics_parser.print_help()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
