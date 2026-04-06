from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .runtime.engine import process_flush
from .write_pipeline.orchestrate import run_rolling_window_pipeline
from .policy.incidents import tag_incident, tag_topic_key
from .policy.hygiene import curated_type_title_hygiene


def handle_store_commands(*, args: Any, memory: Any, doctor_report: Callable[[str], dict]) -> bool:
    """Handle core store/retrieval maintenance commands.

    Returns True when handled, else False.
    """
    cmd = getattr(args, "command", None)

    if cmd == "add":
        bead_id = memory.add_bead(
            type=args.type,
            title=args.title,
            summary=args.summary,
            because=args.because,
            source_turn_ids=args.source_turn_ids,
            tags=args.tags,
            context_tags=args.context_tags,
            session_id=args.session_id,
        )
        print(f"Created bead: {bead_id}")
        return True

    if cmd == "query":
        results = memory.query(type=args.type, status=args.status, tags=args.tags, limit=args.limit)
        for bead in results:
            print(f"{bead['id']}: [{bead['type']}] {bead['title']}")
        return True

    if cmd == "stats":
        print(json.dumps(memory.stats(), indent=2))
        return True

    if cmd == "doctor":
        report = doctor_report(args.root)
        for chk in report.get("checks", []):
            label = "PASS" if chk.get("pass") else "FAIL"
            print(f"{label} {chk.get('name')}")
        print(json.dumps(report, indent=2))
        if not report.get("ok"):
            raise SystemExit(1)
        return True

    if cmd == "heads":
        heads = memory._read_heads()
        if args.topic_id:
            print(json.dumps({"topic_id": args.topic_id, "head": (heads.get("topics") or {}).get(args.topic_id)}, indent=2))
        elif args.goal_id:
            print(json.dumps({"goal_id": args.goal_id, "head": (heads.get("goals") or {}).get(args.goal_id)}, indent=2))
        else:
            print(json.dumps(heads, indent=2))
        return True

    if cmd == "preflight":
        result = memory.preflight_failure_check(plan=args.plan, limit=args.limit, context_tags=args.context_tags)
        print(json.dumps(result, indent=2))
        return True

    if cmd == "constraints":
        print(json.dumps({"ok": True, "constraints": memory.active_constraints(limit=args.limit)}, indent=2))
        return True

    if cmd == "check-plan":
        result = memory.check_plan_constraints(plan=args.plan, limit=args.limit)
        print(json.dumps(result, indent=2))
        return True

    if cmd == "retrieve-context":
        result = memory.retrieve_with_context(
            query_text=args.query,
            context_tags=args.context_tags,
            limit=args.limit,
            strict_first=not args.no_strict_first,
            deep_recall=args.deep_recall,
            max_uncompact_per_turn=args.max_uncompact_per_turn,
            auto_memory_intent=not args.no_auto_memory_intent,
        )
        print(json.dumps(result, indent=2))
        return True

    if cmd == "dream":
        results = memory.dream(
            novel_only=args.novel_only,
            seen_window_runs=args.seen_window_runs,
            max_exposure=args.max_exposure,
        )
        print(json.dumps(results, indent=2))
        return True

    if cmd == "rebuild":
        index = memory.rebuild_index()
        print(f"Rebuilt index with {index['stats']['total_beads']} beads")
        return True

    if cmd == "compact":
        print(json.dumps(memory.compact(session_id=args.session, promote=args.promote)))
        return True

    if cmd == "consolidate":
        result = process_flush(
            root=str(memory.root),
            session_id=args.session,
            promote=bool(args.promote),
            token_budget=int(args.token_budget),
            max_beads=int(args.max_beads),
            source=str(args.source or "admin_cli"),
        )
        print(json.dumps(result, indent=2))
        return True

    if cmd == "rolling-window":
        result = run_rolling_window_pipeline(token_budget=int(args.token_budget), max_beads=int(args.max_beads))
        print(json.dumps(result, indent=2))
        return True

    if cmd == "uncompact":
        result = memory.uncompact(args.id)
        print(json.dumps(result))
        if not result.get("ok"):
            raise SystemExit(1)
        return True

    if cmd == "myelinate":
        print(json.dumps(memory.myelinate(apply=args.apply)))
        return True

    if cmd == "tag":
        if bool(args.incident) == bool(args.topic_key):
            raise SystemExit("tag requires exactly one of --incident or --topic-key")
        if args.incident:
            print(json.dumps(tag_incident(memory.root, incident_id=args.incident, bead_ids=args.bead_ids), indent=2))
        else:
            print(json.dumps(tag_topic_key(memory.root, topic_key=args.topic_key, bead_ids=args.bead_ids), indent=2))
        return True

    if cmd == "hygiene":
        target_ids = list(args.bead_id or [])
        if args.bead_ids_file:
            payload = json.loads(Path(args.bead_ids_file).read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise SystemExit("--bead-ids-file must contain a JSON array")
            target_ids.extend([str(x) for x in payload])
        print(json.dumps(curated_type_title_hygiene(memory.root, target_ids, apply=args.apply), indent=2))
        return True

    return False
