from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph.api import (
    backfill_structural_edges,
    build_graph,
    graph_stats,
    decay_semantic_edges,
    causal_traverse,
    infer_structural_edges,
    sync_structural_pipeline,
    backfill_causal_links,
)
from .retrieval.semantic_index import build_semantic_index, semantic_lookup, semantic_doctor


def handle_graph_command(*, args: Any, memory: Any, graph_parser: Any) -> bool:
    """Handle `core-memory graph ...` commands.

    Returns True when handled (including help output), else False.
    """
    if getattr(args, "command", None) != "graph":
        return False

    if args.graph_cmd == "build":
        b = backfill_structural_edges(memory.root)
        g = build_graph(memory.root, write_snapshot=True)
        print(json.dumps({"ok": True, "backfill": b, "graph": graph_stats(memory.root), "snapshot": g.get("snapshot")}, indent=2))
    elif args.graph_cmd == "stats":
        print(json.dumps(graph_stats(memory.root), indent=2))
    elif args.graph_cmd == "decay":
        print(json.dumps(decay_semantic_edges(memory.root), indent=2))
    elif args.graph_cmd == "semantic-build":
        print(json.dumps(build_semantic_index(memory.root), indent=2))
    elif args.graph_cmd == "semantic-doctor":
        print(json.dumps(semantic_doctor(memory.root), indent=2))
    elif args.graph_cmd == "semantic-lookup":
        print(json.dumps(semantic_lookup(memory.root, query=args.query, k=args.k), indent=2))
    elif args.graph_cmd == "traverse":
        print(json.dumps(causal_traverse(memory.root, anchor_ids=args.anchor), indent=2))
    elif args.graph_cmd == "infer-structural":
        print(json.dumps(infer_structural_edges(memory.root, min_confidence=args.min_confidence, apply=args.apply), indent=2))
    elif args.graph_cmd == "sync-structural":
        out = sync_structural_pipeline(memory.root, apply=args.apply, strict=args.strict)
        print(json.dumps(out, indent=2))
        if args.strict and not out.get("ok"):
            raise SystemExit(2)
    elif args.graph_cmd == "backfill-causal-links":
        target_ids = list(args.bead_id or [])
        if args.bead_ids_file:
            payload = json.loads(Path(args.bead_ids_file).read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise SystemExit("--bead-ids-file must contain a JSON array")
            target_ids.extend([str(x) for x in payload])
        print(
            json.dumps(
                backfill_causal_links(
                    memory.root,
                    apply=args.apply,
                    max_per_target=args.max_per_target,
                    min_overlap=args.min_overlap,
                    require_shared_turn=not bool(args.no_require_shared_turn),
                    include_bead_ids=target_ids,
                ),
                indent=2,
            )
        )
    elif args.graph_cmd == "association-health":
        from .association.health import association_health_report

        print(json.dumps(association_health_report(str(memory.root), session_id=(str(args.session_id or "").strip() or None)), indent=2))
    elif args.graph_cmd == "association-slo-check":
        from .association.slo import association_slo_check

        out = association_slo_check(
            str(memory.root),
            since=str(args.since or "7d"),
            min_agent_authored_rate=float(args.min_agent_authored_rate),
            max_fallback_rate=float(args.max_fallback_rate),
            max_fail_closed_rate=float(args.max_fail_closed_rate),
            min_avg_non_temporal_semantic=float(args.min_avg_non_temporal_semantic),
            max_active_shared_tag_ratio=float(args.max_active_shared_tag_ratio),
        )
        print(json.dumps(out, indent=2))
        if bool(args.strict) and not bool(out.get("ok")):
            raise SystemExit(2)
    elif args.graph_cmd == "neo4j-status":
        from .integrations.neo4j import neo4j_status

        out = neo4j_status()
        print(json.dumps(out, indent=2))
        if bool(args.strict) and not bool(out.get("ok")):
            raise SystemExit(2)
    elif args.graph_cmd == "neo4j-sync":
        from .integrations.neo4j import sync_to_neo4j

        sid = None if bool(args.full) else (str(args.session_id or "").strip() or None)
        bead_ids = None if bool(args.full) else [str(x) for x in (args.bead_id or []) if str(x).strip()]
        out = sync_to_neo4j(
            str(memory.root),
            session_id=sid,
            bead_ids=bead_ids,
            prune=bool(args.prune),
            dry_run=bool(args.dry_run),
        )
        print(json.dumps(out, indent=2))
        if not bool(out.get("ok")):
            raise SystemExit(2)
    else:
        graph_parser.print_help()
    return True
