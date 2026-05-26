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
from .cli_parser_ops import add_async_jobs_command_surfaces
from .cli_parser_extended import (
    add_sidecar_openclaw_parsers,
    add_graph_parser,
    add_metrics_parser,
)
from .cli_handlers_store import handle_store_commands
from .cli_handlers_graph import handle_graph_command
from .cli_handlers_metrics import handle_metrics_command
from .cli_handlers_integrations import handle_integration_commands
from .cli_handlers_ops import handle_ops_commands
from .cli_handlers_semantic import handle_semantic_command
from .cli_diagnostics import canonical_health_report, doctor_report, simple_recall_fallback
from .integrations.mcp.cli import install_payload, status_payload as mcp_status_payload, uninstall_payload, version_payload


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
        metavar="{setup,store,memory,inspect,ingest,integrations,ops,semantic,mcp,dev}",
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

    recall_parser = subparsers.add_parser("recall", help="Run single-verb recall or legacy recall helpers")
    recall_parser.add_argument("--query", help="Natural-language recall query for single-verb recall")
    recall_parser.add_argument("--effort", choices=["low", "medium", "high"], default="medium")
    recall_parser.add_argument("--speaker")
    recall_parser.add_argument("--k", type=int)
    recall_parser.add_argument("--json", action="store_true", help="Emit canonical RecallResult JSON")
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

    ingest_parser = subparsers.add_parser("ingest", help="Import observed conversation artifacts")
    ingest_sub = ingest_parser.add_subparsers(dest="ingest_cmd")
    ingest_transcript_parser = ingest_sub.add_parser("transcript", help="Synchronously ingest a transcript file")
    ingest_transcript_parser.add_argument("path", help="Transcript file path (.json, .jsonl, .md, .txt)")
    ingest_transcript_parser.add_argument("--from", dest="ingest_format", default="auto", choices=["auto", "json", "jsonl", "markdown", "text"], help="Input format")
    ingest_transcript_parser.add_argument("--transcript-id")
    ingest_transcript_parser.add_argument("--session-id")
    ingest_transcript_parser.add_argument("--session-prefix", default="ingest")
    ingest_transcript_parser.add_argument("--flush-policy", choices=["none", "end_only", "per_session"], default="none")

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

    migrate_parser = subparsers.add_parser("migrate", help="Populate Qdrant and Kuzu from existing bead store (idempotent)")
    migrate_parser.add_argument("--dry-run", action="store_true", help="Report counts without writing")
    migrate_parser.add_argument("--skip-vectors", action="store_true", help="Skip Qdrant population")
    migrate_parser.add_argument("--skip-graph", action="store_true", help="Skip Kuzu/graph population")

    ops_parser = subparsers.add_parser("ops", help="Operational maintenance and diagnostics")
    ops_sub = ops_parser.add_subparsers(dest="ops_cmd")
    ops_sub.add_parser("doctor", help="Run local store health checks")
    ops_sub.add_parser("rebuild", help="Rebuild index from events")
    ops_sub.add_parser("archive-index-rebuild", help="Rebuild archive O(1) index")
    ops_sub.add_parser("graph-sync", help="Sync structural pipeline")

    semantic_parser = subparsers.add_parser("semantic", help="Inspect and operate semantic indexing")
    semantic_sub = semantic_parser.add_subparsers(dest="semantic_cmd")
    semantic_sub.add_parser("status", help="Show semantic manifest and queue status as JSON")
    semantic_rebuild = semantic_sub.add_parser("rebuild", help="Enqueue a semantic index rebuild")
    semantic_rebuild.add_argument("--mode", choices=["delta", "reconcile"], default="delta")
    semantic_rebuild.add_argument("--wait", action="store_true", help="Drain the semantic worker immediately after enqueue")
    semantic_tail = semantic_sub.add_parser("tail", help="Show recent semantic lifecycle events as JSON")
    semantic_tail.add_argument("-n", type=int, default=20)
    semantic_sub.add_parser("doctor", help="Show semantic mode/backend diagnostics as JSON")

    mcp_parser = subparsers.add_parser("mcp", help="Install and inspect the MCP protocol server")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_cmd")
    mcp_install = mcp_sub.add_parser("install", help="Install Core Memory MCP into supported clients")
    mcp_install.add_argument("--client", choices=["claude-code", "cursor", "windsurf", "open-webui"])
    mcp_install.add_argument("--root")
    mcp_install.add_argument("--port", type=int, default=8000)
    mcp_install.add_argument("--no-start", action="store_true")
    mcp_install.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    mcp_status = mcp_sub.add_parser("status", help="Check Core Memory MCP server status")
    mcp_status.add_argument("--port", type=int, default=8000)
    mcp_uninstall = mcp_sub.add_parser("uninstall", help="Remove Core Memory MCP client config")
    mcp_uninstall.add_argument("--client", choices=["claude-code", "cursor", "windsurf", "open-webui"])
    mcp_uninstall.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    mcp_sub.add_parser("version", help="Show MCP spec and SDK versions")

    dev_parser = subparsers.add_parser("dev", help="Advanced developer-facing command surfaces")
    dev_parser.add_argument(
        "_dev_reserved",
        nargs="*",
        help=argparse.SUPPRESS,
    )

    mem_parser, _ = add_memory_command_surface(
        subparsers,
        name="memory",
        help_text="Canonical memory interface (search/trace/execute)",
        dest="memory_cmd",
    )
    
    legacy_help = argparse.SUPPRESS

    add_async_jobs_command_surfaces(
        ops_sub=ops_sub,
        subparsers=subparsers,
        legacy_help=legacy_help,
    )

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
    sidecar_parser, oc_parser = add_sidecar_openclaw_parsers(
        subparsers,
        legacy_help=legacy_help,
    )

    graph_parser = add_graph_parser(
        subparsers,
        legacy_help=legacy_help,
    )

    metrics_parser = add_metrics_parser(
        subparsers,
        legacy_help=legacy_help,
    )

    args = parser.parse_args(rewrite_legacy_dev_memory_argv(sys.argv[1:]))

    if ensure_group_subcommand_selected(
        args,
        {
            "setup": setup_parser,
            "store": store_parser,
            "recall": recall_parser,
            "inspect": inspect_parser,
            "ingest": ingest_parser,
            "integrations": integrations_parser,
            "ops": ops_parser,
            "semantic": semantic_parser,
            "mcp": mcp_parser,
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

    if args.command == "migrate":
        from core_memory.cli_handlers_migrate import handle_migrate
        sys.exit(handle_migrate(args))

    if args.command == "ingest":
        if args.ingest_cmd == "transcript":
            from core_memory.integrations.mcp.tools.ingest import ingest_handler

            print(
                json.dumps(
                    ingest_handler(
                        {
                            "root": args.root,
                            "path": args.path,
                            "from": args.ingest_format,
                            "transcript_id": args.transcript_id,
                            "session_id": args.session_id,
                            "session_prefix": args.session_prefix,
                            "flush_policy": args.flush_policy,
                        }
                    ),
                    indent=2,
                )
            )
            return
        ingest_parser.print_help()
        return

    if handle_semantic_command(args=args, root=args.root):
        return

    if args.command == "mcp":
        if args.mcp_cmd == "version":
            print(json.dumps(version_payload(), indent=2))
            return
        if args.mcp_cmd == "status":
            print(json.dumps(mcp_status_payload(port=args.port), indent=2))
            return
        if args.mcp_cmd == "install":
            print(json.dumps(install_payload(client=args.client, root=args.root, port=args.port, no_start=args.no_start, dry_run=args.dry_run), indent=2))
            return
        if args.mcp_cmd == "uninstall":
            print(json.dumps(uninstall_payload(client=args.client, dry_run=args.dry_run), indent=2))
            return
        mcp_parser.print_help()
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

    elif handle_ops_commands(args=args, memory=memory):
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
