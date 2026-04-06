"""
Core-Memory CLI.

Canonical command-line interface for core-memory.
Entry point only - does not export from __init__.py to avoid circular imports.

Command families:
    core        - add, query, stats, rebuild, compact, uncompact
    retrieval   - search, trace, execute, retrieve-context, constraints, check-plan, preflight
    graph       - graph build/stats/decay/traverse/sync/infer
    maintenance - hygiene, myelinate, archive-index-rebuild
    integration - sidecar (coordinator hooks), memory (typed skill interface)
    metrics     - comprehensive metrics/evaluation/promotion tooling
    advanced    - dream (novel association discovery)

Examples:
    core-memory add --type decision --title "My decision" --summary "Point 1" "Point 2"
    core-memory query --type decision --limit 10
    core-memory graph build
    core-memory metrics promotion-slate --query "memory"
"""

import argparse
import json
import sys

# Use relative import to avoid circular import
from .persistence.store import MemoryStore, DEFAULT_ROOT
from .retrieval.tools.memory import (
    execute as memory_execute_tool,
    execute as memory_execute,
    search as memory_search_tool,
    trace as memory_trace_tool,
)
from .cli_compat import (
    rewrite_legacy_dev_memory_argv,
    ensure_group_subcommand_selected,
    apply_grouped_aliases,
)
from .cli_memory_handlers import handle_memory_command
from .cli_parser_memory import add_memory_command_surface
from .cli_handlers_store import handle_store_commands
from .cli_handlers_graph import handle_graph_command
from .cli_handlers_metrics import handle_metrics_command
from .cli_handlers_integrations import handle_integration_commands
from .cli_diagnostics import canonical_health_report, doctor_report, simple_recall_fallback


# Compatibility wrappers for tests/legacy imports during CLI boundary split.
def _canonical_health_report(root: str, write_path: str | None = None) -> dict:
    return canonical_health_report(root, write_path=write_path)


def _doctor_report(root: str) -> dict:
    return doctor_report(root)


def _simple_recall_fallback(memory: MemoryStore, query_text: str, limit: int = 8) -> dict:
    return simple_recall_fallback(memory, query_text=query_text, limit=limit)


class _CliHelpFormatter(argparse.HelpFormatter):
    """Hide suppressed legacy subcommands from root help output."""

    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            original = list(action._choices_actions)
            try:
                action._choices_actions = [
                    a
                    for a in original
                    if getattr(a, "help", None) not in {argparse.SUPPRESS, "==SUPPRESS=="}
                ]
                return super()._format_action(action)
            finally:
                action._choices_actions = original
        return super()._format_action(action)


class _CliParser(argparse.ArgumentParser):
    """ArgumentParser variant that consistently applies CLI help filtering."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", _CliHelpFormatter)
        super().__init__(*args, **kwargs)


def main():
    """CLI entry point for core-memory command."""
    parser = _CliParser(description="Core-Memory CLI")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Memory root directory")

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="{setup,store,memory,inspect,integrations,ops,dev}",
    )

    # Grouped surface (preferred)
    setup_parser = subparsers.add_parser("setup", help="Initialize/configure/validate local Core Memory store")
    setup_sub = setup_parser.add_subparsers(dest="setup_cmd")
    setup_sub.add_parser("init", help="Initialize store directories at --root")
    setup_sub.add_parser("doctor", help="Run local store health checks")
    setup_sub.add_parser("paths", help="Show resolved store paths")

    store_parser = subparsers.add_parser("store", help="Create/mutate stored memory records")
    store_sub = store_parser.add_subparsers(dest="store_cmd")
    store_add = store_sub.add_parser("add", help="Add a bead")
    store_add.add_argument("--type", required=True)
    store_add.add_argument("--title", required=True)
    store_add.add_argument("--summary", nargs="*")
    store_add.add_argument("--because", nargs="*")
    store_add.add_argument("--source-turn-ids", nargs="*")
    store_add.add_argument("--tags", nargs="*")
    store_add.add_argument("--context-tags", nargs="*")
    store_add.add_argument("--session-id")
    store_sub.add_parser("stats", help="Show store statistics")
    store_compact = store_sub.add_parser("compact", help="Compact beads")
    store_compact.add_argument("--session")
    store_compact.add_argument("--promote", action="store_true")
    store_uncompact = store_sub.add_parser("uncompact", help="Restore compacted bead detail")
    store_uncompact.add_argument("--id", required=True)
    store_consolidate = store_sub.add_parser("consolidate", help="Run canonical runtime consolidation/flush pipeline")
    store_consolidate.add_argument("--session", required=True)
    store_consolidate.add_argument("--promote", action="store_true")
    store_consolidate.add_argument("--token-budget", type=int, default=1200)
    store_consolidate.add_argument("--max-beads", type=int, default=12)
    store_consolidate.add_argument("--source", default="admin_cli")
    store_rw = store_sub.add_parser("rolling-window", help="Run rolling window maintenance pipeline")
    store_rw.add_argument("--token-budget", type=int, default=1200)
    store_rw.add_argument("--max-beads", type=int, default=12)

    recall_parser = subparsers.add_parser("recall", help=argparse.SUPPRESS)
    recall_sub = recall_parser.add_subparsers(dest="recall_cmd")
    recall_search = recall_sub.add_parser("search", help="Canonical memory search")
    recall_search.add_argument("query", nargs="?", default="", help="Natural-language query (plug-and-play mode)")
    recall_search.add_argument("--intent", default="remember", help="Search intent for simple mode (default: remember)")
    recall_search.add_argument("--k", type=int, default=8, help="Result count for simple mode")
    recall_search.add_argument("--explain", action="store_true")
    recall_heads = recall_sub.add_parser("heads", help="Show topic/goal HEAD pointers")
    recall_heads.add_argument("--topic-id")
    recall_heads.add_argument("--goal-id")
    recall_trace = recall_sub.add_parser("trace", help="Trace causal chains from query or anchor ids")
    recall_trace.add_argument("query", nargs="?", default="")
    recall_trace.add_argument("--k", type=int, default=8)
    recall_trace.add_argument("--anchor-ids", nargs="*")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect stored artifacts")
    inspect_sub = inspect_parser.add_subparsers(dest="inspect_cmd")
    inspect_list = inspect_sub.add_parser("list", help="List/query beads")
    inspect_list.add_argument("--type")
    inspect_list.add_argument("--status")
    inspect_list.add_argument("--tags", nargs="*")
    inspect_list.add_argument("--limit", type=int, default=20)
    inspect_sub.add_parser("stats", help="Show statistics")
    inspect_sub.add_parser("health", help="Run local store health checks")

    integrations_parser = subparsers.add_parser("integrations", help="Integration setup and bridge-facing operations")
    integrations_sub = integrations_parser.add_subparsers(dest="integrations_cmd")
    int_openclaw = integrations_sub.add_parser("openclaw", help="OpenClaw integration commands")
    int_openclaw_sub = int_openclaw.add_subparsers(dest="integrations_openclaw_cmd")
    int_oc_onboard = int_openclaw_sub.add_parser("onboard", help="Install/enable Core Memory bridge plugin in OpenClaw")
    int_oc_onboard.add_argument("--openclaw-bin", default="openclaw")
    int_oc_onboard.add_argument("--plugin-dir", help="Path to core-memory bridge plugin directory")
    int_oc_onboard.add_argument("--replace-memory-core", action="store_true")
    int_oc_onboard.add_argument("--dry-run", action="store_true")

    int_api = integrations_sub.add_parser("api", help="Low-level integration API wrappers")
    int_api_sub = int_api.add_subparsers(dest="integrations_api_cmd")
    int_api_emit = int_api_sub.add_parser("emit-turn", help="Emit finalized turn from envelope JSON")
    int_api_emit.add_argument("--from-file", required=True, help="Path to turn envelope JSON")

    int_migrate = integrations_sub.add_parser("migrate", help="Integration migration helpers")
    int_migrate_sub = int_migrate.add_subparsers(dest="integrations_migrate_cmd")
    int_migrate_sub.add_parser("rebuild-turn-indexes", help="Rebuild .turns per-session indexes")
    int_migrate_sub.add_parser("backfill-bead-session-ids", help="Backfill missing bead session_id values")

    ops_parser = subparsers.add_parser("ops", help="Operational maintenance and diagnostics")
    ops_sub = ops_parser.add_subparsers(dest="ops_cmd")
    ops_sub.add_parser("doctor", help="Run local store health checks")
    ops_sub.add_parser("rebuild", help="Rebuild index from events")
    ops_sub.add_parser("archive-index-rebuild", help="Rebuild archive O(1) index")
    ops_sub.add_parser("graph-sync", help="Sync structural pipeline")

    dev_parser = subparsers.add_parser("dev", help="Advanced developer-facing command surfaces")
    dev_parser.add_argument(
        "_dev_reserved",
        nargs="*",
        help=argparse.SUPPRESS,
    )
    
    legacy_help = argparse.SUPPRESS

    # add command (legacy top-level; use `store add`)
    add_parser = subparsers.add_parser("add", help=legacy_help)
    add_parser.add_argument("--type", required=True, help="Bead type")
    add_parser.add_argument("--title", required=True, help="Bead title")
    add_parser.add_argument("--summary", nargs="*", help="Summary points")
    add_parser.add_argument("--because", nargs="*", help="Structured rationale points")
    add_parser.add_argument("--source-turn-ids", nargs="*", help="Provenance turn IDs")
    add_parser.add_argument("--tags", nargs="*", help="Tags")
    add_parser.add_argument("--context-tags", nargs="*", help="Environment/context tags")
    add_parser.add_argument("--session-id", help="Session ID")
    
    # query command (legacy top-level; use `inspect list`)
    query_parser = subparsers.add_parser("query", help=legacy_help)
    query_parser.add_argument("--type", help="Filter by type")
    query_parser.add_argument("--status", help="Filter by status")
    query_parser.add_argument("--tags", nargs="*", help="Filter by tags")
    query_parser.add_argument("--limit", type=int, default=20)
    
    # stats command (legacy top-level; use `inspect stats` or `store stats`)
    subparsers.add_parser("stats", help=legacy_help)

    # contributor-local health checks (legacy top-level; use `setup doctor` or `ops doctor`)
    subparsers.add_parser("doctor", help=legacy_help)

    # heads command (legacy top-level; use `recall heads`)
    heads_parser = subparsers.add_parser("heads", help=legacy_help)
    heads_parser.add_argument("--topic-id", help="Lookup specific topic HEAD")
    heads_parser.add_argument("--goal-id", help="Lookup specific goal HEAD")

    # preflight failure check (legacy top-level)
    preflight_parser = subparsers.add_parser("preflight", help=legacy_help)
    preflight_parser.add_argument("--plan", required=True, help="Normalized plan text to check")
    preflight_parser.add_argument("--context-tags", nargs="*", help="Optional environment/context tags")
    preflight_parser.add_argument("--limit", type=int, default=5)

    # phase-3 advisory constraints (legacy top-level)
    constraints_parser = subparsers.add_parser("constraints", help=legacy_help)
    constraints_parser.add_argument("--limit", type=int, default=20)

    check_plan_parser = subparsers.add_parser("check-plan", help=legacy_help)
    check_plan_parser.add_argument("--plan", required=True)
    check_plan_parser.add_argument("--limit", type=int, default=20)

    # phase-4 environment scoped retrieval (legacy top-level)
    retrieve_ctx_parser = subparsers.add_parser("retrieve-context", help=legacy_help)
    retrieve_ctx_parser.add_argument("--query", default="")
    retrieve_ctx_parser.add_argument("--context-tags", nargs="*", help="Requested environment tags")
    retrieve_ctx_parser.add_argument("--limit", type=int, default=20)
    retrieve_ctx_parser.add_argument("--no-strict-first", action="store_true")
    retrieve_ctx_parser.add_argument("--deep-recall", action="store_true", help="Enable bounded deep recall (auto-uncompact compacted/archived beads)")
    retrieve_ctx_parser.add_argument("--max-uncompact-per-turn", type=int, default=2, help="Bounded deep recall budget per call")
    retrieve_ctx_parser.add_argument("--no-auto-memory-intent", action="store_true", help="Disable memory-intent heuristic trigger")
    
    # dream command (legacy top-level)
    dream_parser = subparsers.add_parser("dream", help=legacy_help)
    dream_parser.add_argument("--novel-only", action="store_true", help="Exclude previously surfaced bead pairs")
    dream_parser.add_argument("--seen-window-runs", type=int, default=0, help="Only consider the last N Dreamer runs for novelty dedupe (0=all)")
    dream_parser.add_argument("--max-exposure", type=int, default=-1, help="Skip candidates where either bead has been surfaced more than this count (-1=disabled)")
    
    # rebuild command (legacy top-level; use `ops rebuild`)
    subparsers.add_parser("rebuild", help=legacy_help)

    # compact command (legacy top-level; use `store compact`)
    compact_parser = subparsers.add_parser("compact", help=legacy_help)
    compact_parser.add_argument("--session", help="Compact only this session")
    compact_parser.add_argument("--promote", action="store_true", help="Promote compacted beads")

    # canonical consolidate command (legacy top-level; use `store consolidate`)
    consolidate_parser = subparsers.add_parser("consolidate", help=legacy_help)
    consolidate_parser.add_argument("--session", required=True, help="Session id")
    consolidate_parser.add_argument("--promote", action="store_true", help="Enable promote mode")
    consolidate_parser.add_argument("--token-budget", type=int, default=1200)
    consolidate_parser.add_argument("--max-beads", type=int, default=12)
    consolidate_parser.add_argument("--source", default="admin_cli")

    # rolling-window refresh command (legacy top-level; use `store rolling-window`)
    rw_parser = subparsers.add_parser("rolling-window", help=legacy_help)
    rw_parser.add_argument("--token-budget", type=int, default=1200)
    rw_parser.add_argument("--max-beads", type=int, default=12)

    # uncompact command (legacy top-level; use `store uncompact`)
    uncompact_parser = subparsers.add_parser("uncompact", help=legacy_help)
    uncompact_parser.add_argument("--id", required=True, help="Bead ID")

    # myelinate command (legacy top-level)
    myelinate_parser = subparsers.add_parser("myelinate", help=legacy_help)
    myelinate_parser.add_argument("--apply", action="store_true", help="Apply changes (default dry-run)")

    # sidecar integration command (legacy top-level; use `dev` surfaces)
    sidecar_parser = subparsers.add_parser("sidecar", help=legacy_help)
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

    # openclaw integration onboarding (legacy top-level; use `integrations openclaw`)
    oc_parser = subparsers.add_parser("openclaw", help=legacy_help)
    oc_sub = oc_parser.add_subparsers(dest="openclaw_cmd")
    oc_onboard = oc_sub.add_parser("onboard", help="Install/enable Core Memory bridge plugin in OpenClaw")
    oc_onboard.add_argument("--openclaw-bin", default="openclaw")
    oc_onboard.add_argument("--plugin-dir", help="Path to core-memory bridge plugin directory")
    oc_onboard.add_argument("--replace-memory-core", action="store_true", help="Disable stock memory-core plugin")
    oc_onboard.add_argument("--dry-run", action="store_true")

    tag_parser = subparsers.add_parser("tag", help=legacy_help)
    tag_parser.add_argument("--incident", help="Incident ID")
    tag_parser.add_argument("--topic-key", help="Topic key tag")
    tag_parser.add_argument("bead_ids", nargs="+", help="Bead IDs to update")

    hygiene_parser = subparsers.add_parser("hygiene", help=legacy_help)
    hygiene_parser.add_argument("--bead-id", action="append", help="Target bead id (repeatable)")
    hygiene_parser.add_argument("--bead-ids-file", help="Path to JSON array of bead IDs")
    hygiene_parser.add_argument("--apply", action="store_true")

    mem_parser, _ = add_memory_command_surface(
        subparsers,
        name="memory",
        help_text="Canonical memory interface (search/trace/execute)",
        dest="memory_cmd",
    )

    # graph command (legacy top-level)
    graph_parser = subparsers.add_parser("graph", help=legacy_help)
    graph_sub = graph_parser.add_subparsers(dest="graph_cmd")
    graph_sub.add_parser("build", help="Backfill structural edges and rebuild graph snapshot")
    graph_sub.add_parser("stats", help="Show graph edge/node stats")
    graph_sub.add_parser("decay", help="Run semantic edge decay pass")
    g_sem_build = graph_sub.add_parser("semantic-build", help="Build semantic lookup index")
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

    # metrics command (legacy top-level; use `ops`/`dev` surfaces)
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

    metrics_archive_rebuild = metrics_sub.add_parser("archive-index-rebuild", help="Rebuild archive O(1) index from archive.jsonl")

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
    
    args = parser.parse_args(rewrite_legacy_dev_memory_argv(sys.argv[1:]))

    if ensure_group_subcommand_selected(
        args,
        {
            "setup": setup_parser,
            "store": store_parser,
            "recall": recall_parser,
            "inspect": inspect_parser,
            "integrations": integrations_parser,
            "ops": ops_parser,
            "dev": dev_parser,
        },
    ):
        return

    if apply_grouped_aliases(args, openclaw_group_parser=int_openclaw):
        return

    # setup init/paths are direct grouped-surface operations and intentionally
    # stay local to the CLI shell.
    if args.command == "setup" and args.setup_cmd in {"init", "paths"}:
        memory = MemoryStore(root=args.root)
        print(json.dumps({"ok": True, "root": args.root, "beads_dir": str(memory.beads_dir), "turns_dir": str(memory.turns_dir)}, indent=2))
        return

    memory = MemoryStore(root=args.root)
    
    if handle_store_commands(args=args, memory=memory, doctor_report=doctor_report):
        pass

    elif handle_integration_commands(
        args=args,
        memory=memory,
        sidecar_parser=sidecar_parser,
        openclaw_parser=oc_parser,
    ):
        pass

    elif handle_memory_command(
        args=args,
        memory=memory,
        mem_parser=mem_parser,
        simple_recall_fallback=simple_recall_fallback,
        memory_search_tool=memory_search_tool,
        memory_trace_tool=memory_trace_tool,
        memory_execute=memory_execute,
    ):
        pass

    elif handle_graph_command(args=args, memory=memory, graph_parser=graph_parser):
        pass

    elif handle_metrics_command(
        args=args,
        memory=memory,
        metrics_parser=metrics_parser,
        canonical_health_report=canonical_health_report,
    ):
        pass

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
