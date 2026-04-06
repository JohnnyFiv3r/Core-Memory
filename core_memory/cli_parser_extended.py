from __future__ import annotations

import argparse


def add_sidecar_openclaw_parsers(
    subparsers: argparse._SubParsersAction,
    *,
    legacy_help: str,
) -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """Add legacy top-level sidecar/openclaw parser trees."""
    sidecar_parser = subparsers.add_parser("sidecar", help=legacy_help)
    sc_sub = sidecar_parser.add_subparsers(dest="sidecar_cmd")

    sc_finalize = sc_sub.add_parser("finalize", help="Canonical finalized-turn ingest + process")
    sc_finalize.add_argument("--session-id", required=True)
    sc_finalize.add_argument("--turn-id", required=True)
    sc_finalize.add_argument("--transaction-id")
    sc_finalize.add_argument("--trace-id")
    sc_finalize.add_argument("--user-query", required=True)
    sc_finalize.add_argument("--assistant-final", required=True)
    sc_finalize.add_argument("--trace-depth", type=int, default=0)
    sc_finalize.add_argument("--origin", default="USER_TURN")

    sc_turn = sc_sub.add_parser("turn", help="turn_finalized convenience wrapper (legacy)")
    sc_turn.add_argument("--session-id", required=True)
    sc_turn.add_argument("--turn-id", required=True)
    sc_turn.add_argument("--transaction-id")
    sc_turn.add_argument("--trace-id")
    sc_turn.add_argument("--user-query", required=True)
    sc_turn.add_argument("--assistant-final", required=True)
    sc_turn.add_argument("--trace-depth", type=int, default=0)
    sc_turn.add_argument("--origin", default="USER_TURN")
    sc_turn.add_argument("--meta-constraint-violation", action="store_true")
    sc_turn.add_argument("--meta-wrong-transfer", action="store_true")
    sc_turn.add_argument("--meta-goal-carryover", action="store_true")
    sc_turn.add_argument("--store-full-text", choices=["true", "false"], default="true")

    oc_parser = subparsers.add_parser("openclaw", help=legacy_help)
    oc_sub = oc_parser.add_subparsers(dest="openclaw_cmd")
    oc_onboard = oc_sub.add_parser("onboard", help="Install/enable Core Memory bridge plugin in OpenClaw")
    oc_onboard.add_argument("--openclaw-bin", default="openclaw")
    oc_onboard.add_argument("--plugin-dir", help="Path to core-memory bridge plugin directory")
    oc_onboard.add_argument("--replace-memory-core", action="store_true", help="Disable stock memory-core plugin")
    oc_onboard.add_argument("--dry-run", action="store_true")

    return sidecar_parser, oc_parser


def add_graph_parser(subparsers: argparse._SubParsersAction, *, legacy_help: str) -> argparse.ArgumentParser:
    """Add legacy top-level graph parser tree."""
    graph_parser = subparsers.add_parser("graph", help=legacy_help)
    graph_sub = graph_parser.add_subparsers(dest="graph_cmd")
    graph_sub.add_parser("build", help="Backfill structural edges and rebuild graph snapshot")
    graph_sub.add_parser("stats", help="Show graph edge/node stats")
    graph_sub.add_parser("decay", help="Run semantic edge decay pass")
    graph_sub.add_parser("semantic-build", help="Build semantic lookup index")
    graph_sub.add_parser("semantic-doctor", help="Show semantic mode/backend diagnostics")
    g_sem_lookup = graph_sub.add_parser("semantic-lookup", help="Semantic lookup by query")
    g_sem_lookup.add_argument("--query", required=True)
    g_sem_lookup.add_argument("--k", type=int, default=8)
    g_traverse = graph_sub.add_parser("traverse", help="Run structural-first causal traversal from anchors")
    g_traverse.add_argument("--anchor", nargs="+", required=True)
    g_infer = graph_sub.add_parser("infer-structural", help="Run deterministic structural edge inference (safe-gated)")
    g_infer.add_argument("--min-confidence", type=float, default=0.9)
    g_infer.add_argument("--apply", action="store_true")
    g_sync = graph_sub.add_parser("sync-structural", help="Sync associations->links->immutable structural edges->graph")
    g_sync.add_argument("--apply", action="store_true")
    g_sync.add_argument("--strict", action="store_true")
    g_backfill_causal = graph_sub.add_parser("backfill-causal-links", help="Programmatic causal backfill for existing content")
    g_backfill_causal.add_argument("--apply", action="store_true")
    g_backfill_causal.add_argument("--max-per-target", type=int, default=3)
    g_backfill_causal.add_argument("--min-overlap", type=int, default=2)
    g_backfill_causal.add_argument("--no-require-shared-turn", action="store_true")
    g_backfill_causal.add_argument("--bead-id", action="append", help="Limit proposals to pairs touching these bead IDs")
    g_backfill_causal.add_argument("--bead-ids-file", help="Path to JSON array of bead IDs for targeted mode")
    g_assoc_health = graph_sub.add_parser("association-health", help="Report association quality and isolation stats")
    g_assoc_health.add_argument("--session-id", help="Optional session scope")
    g_assoc_slo = graph_sub.add_parser("association-slo-check", help="Evaluate association quality SLO gates")
    g_assoc_slo.add_argument("--since", default="7d")
    g_assoc_slo.add_argument("--min-agent-authored-rate", type=float, default=0.8)
    g_assoc_slo.add_argument("--max-fallback-rate", type=float, default=0.1)
    g_assoc_slo.add_argument("--max-fail-closed-rate", type=float, default=0.25)
    g_assoc_slo.add_argument("--min-avg-non-temporal-semantic", type=float, default=1.0)
    g_assoc_slo.add_argument("--max-active-shared-tag-ratio", type=float, default=0.4)
    g_assoc_slo.add_argument("--strict", action="store_true", help="Exit code 2 when SLO check fails")
    g_neo4j_status = graph_sub.add_parser("neo4j-status", help="Check Neo4j shadow adapter config/connectivity")
    g_neo4j_status.add_argument("--strict", action="store_true", help="Return exit code 2 when status is not ok")
    g_neo4j_sync = graph_sub.add_parser("neo4j-sync", help="Sync Core Memory bead/association projection into Neo4j")
    g_neo4j_sync.add_argument("--session-id", help="Optional session scope filter")
    g_neo4j_sync.add_argument("--bead-id", action="append", help="Optional bead_id filter (repeatable)")
    g_neo4j_sync.add_argument("--prune", action="store_true", help="Prune non-matching shadow graph data (optional)")
    g_neo4j_sync.add_argument("--full", action="store_true", help="Ignore filters and sync full projection")
    g_neo4j_sync.add_argument("--dry-run", action="store_true", help="Plan projection without remote writes")
    return graph_parser


def add_metrics_parser(subparsers: argparse._SubParsersAction, *, legacy_help: str) -> argparse.ArgumentParser:
    """Add legacy top-level metrics parser tree."""
    metrics_parser = subparsers.add_parser("metrics", help=legacy_help)
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

    metrics_schema = metrics_sub.add_parser("schema-quality", help="Report required-field warnings and promotion gate blocks")
    metrics_schema.add_argument("--write", help="Optional path to write markdown report")

    metrics_rebalance = metrics_sub.add_parser("rebalance-promotions", help="Phase-B scoring rebalance for promoted beads")
    metrics_rebalance.add_argument("--apply", action="store_true", help="Apply demotions; default is dry-run")

    metrics_slate = metrics_sub.add_parser("promotion-slate", help="Build bounded candidate promotion slate (advisory)")
    metrics_slate.add_argument("--limit", type=int, default=20)
    metrics_slate.add_argument("--query", default="")

    metrics_decide = metrics_sub.add_parser("decide-promotion", help="Apply agent promotion decision for one bead")
    metrics_decide.add_argument("--id", required=True, help="Bead ID")
    metrics_decide.add_argument("--decision", required=True, choices=["promote", "keep_candidate", "archive"])
    metrics_decide.add_argument("--reason", default="", help="Required for promote/archive")
    metrics_decide.add_argument("--consideration", nargs="*", help="Optional decision considerations")

    metrics_decide_bulk = metrics_sub.add_parser("decide-promotion-bulk", help="Apply agent promotion decisions from JSON file")
    metrics_decide_bulk.add_argument("--file", required=True, help="Path to JSON array of {bead_id,decision,reason?,considerations?}")

    metrics_promo_kpis = metrics_sub.add_parser("promotion-kpis", help="Report promotion decision KPIs and recommendation alignment")
    metrics_promo_kpis.add_argument("--limit", type=int, default=500)

    metrics_sub.add_parser("archive-index-rebuild", help="Rebuild archive O(1) index from archive.jsonl")

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

    metrics_canonical = metrics_sub.add_parser("canonical-health", help="Run canonical contract health checks")
    metrics_canonical.add_argument("--write", help="Optional JSON output path")

    metrics_dreamer_eval = metrics_sub.add_parser("dreamer-eval", help="Compute Dreamer behavior-change eval summary")
    metrics_dreamer_eval.add_argument("--since", default="30d")
    metrics_dreamer_eval.add_argument("--strict", action="store_true", help="Exit code 2 when core metrics are all zero")
    metrics_dreamer_eval.add_argument("--write", help="Optional JSON output path")

    metrics_longitudinal = metrics_sub.add_parser("longitudinal-benchmark-v2", help="Compute longitudinal benchmark cohort comparison")
    metrics_longitudinal.add_argument("--since", default="30d")
    metrics_longitudinal.add_argument("--strict", action="store_true", help="Exit code 2 when dreamer cohort does not beat no-memory baseline")
    metrics_longitudinal.add_argument("--write", help="Optional JSON output path")

    return metrics_parser
