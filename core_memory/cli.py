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
    add_parser.add_argument("--session-id", help="Session ID")
    
    # query command
    query_parser = subparsers.add_parser("query", help="Query beads")
    query_parser.add_argument("--type", help="Filter by type")
    query_parser.add_argument("--status", help="Filter by status")
    query_parser.add_argument("--tags", nargs="*", help="Filter by tags")
    query_parser.add_argument("--limit", type=int, default=20)
    
    # stats command
    subparsers.add_parser("stats", help="Show statistics")
    
    # dream command
    subparsers.add_parser("dream", help="Run Dreamer analysis")
    
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
    
    elif args.command == "dream":
        results = memory.dream()
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
        else:
            metrics_parser.print_help()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
