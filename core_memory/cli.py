"""
Core-Memory CLI.

Canonical command-line interface for core-memory.
Entry point only - does not export from __init__.py to avoid circular imports.

Command families:
    core        - add, query, stats, rebuild, compact, uncompact
    retrieval   - reason, retrieve-context, constraints, check-plan, preflight
    graph       - graph build/stats/decay/traverse/sync/infer
    maintenance - hygiene, myelinate, migrate-store, archive-index-rebuild
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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Use relative import to avoid circular import
from .persistence.store import MemoryStore, DEFAULT_ROOT
from .persistence.archive_index import rebuild_archive_index
from .graph.api import backfill_structural_edges, build_graph, graph_stats, decay_semantic_edges, causal_traverse, infer_structural_edges, sync_structural_pipeline, backfill_causal_links
from .retrieval.semantic_index import build_semantic_index, semantic_lookup
from .retrieval.tools.memory_reason import memory_reason
from .retrieval.tools.memory import execute as memory_execute_tool
from .runtime.engine import process_turn_finalized, process_flush
from .write_pipeline.orchestrate import run_rolling_window_pipeline
from .policy.incidents import tag_incident, tag_topic_key
from .policy.hygiene import curated_type_title_hygiene
from .integrations.openclaw_runtime import (
    coordinator_finalize_hook,
    finalize_and_process_turn,
)
from .retrieval.pipeline import memory_get_search_form, memory_search_typed, memory_execute
from .integrations.openclaw_onboard import run_openclaw_onboard, render_onboard_report


def _write_legacy_readiness_snapshot(root: str, payload: dict) -> dict:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reports = Path(root) / "docs" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    json_path = reports / f"legacy-closure-readiness-{day}.json"
    md_path = reports / f"legacy-closure-readiness-{day}.md"

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md = [
        "# Legacy Closure Readiness",
        "",
        f"Date (UTC): {day}",
        f"Ready for legacy removal: {bool(payload.get('ready_for_legacy_removal'))}",
        "",
        "## Summary",
        f"- Shim usage count: {(payload.get('summary') or {}).get('shim_usage_count', 0)}",
        f"- Legacy dispatch count: {(payload.get('summary') or {}).get('legacy_dispatch_count', 0)}",
        f"- Legacy dispatch blocked count: {(payload.get('summary') or {}).get('legacy_dispatch_blocked_count', 0)}",
        "",
        "## Trigger status counts",
        json.dumps(payload.get("trigger_status_counts") or {}, indent=2),
    ]
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def _canonical_health_report(root: str, write_path: str | None = None) -> dict:
    import tempfile

    checks = {}
    with tempfile.TemporaryDirectory() as td:
        # Turn + flush + rolling window + archive ergonomics
        t1 = process_turn_finalized(
            root=td,
            session_id="health",
            turn_id="t1",
            user_query="remember canonical decision",
            assistant_final="Decision: keep canonical path and stable retrieval.",
        )
        f1 = process_flush(root=td, session_id="health", promote=True, token_budget=800, max_beads=10, source="canonical_health")
        f2 = process_flush(root=td, session_id="health", promote=True, token_budget=800, max_beads=10, source="canonical_health")

        phase_trace = ((f1.get("result") or {}).get("phase_trace") or [])
        checks["turn_path"] = bool(t1.get("ok"))
        checks["flush_once_per_cycle"] = bool(f2.get("skipped") and f2.get("reason") == "already_flushed_for_latest_turn")
        checks["rolling_window_maintenance"] = bool("rolling_window_write" in phase_trace)
        checks["archive_ergonomics"] = bool("archive_compact_session" in phase_trace and "archive_compact_historical" in phase_trace)

        # Full retrieval path via tool execute
        req = {"query": "canonical decision", "session_id": "health", "limit": 5}
        ret = memory_execute_tool(req, root=td, explain=True)
        checks["retrieval_path"] = bool((ret.get("ok") is True) or (ret.get("results") is not None) or (ret.get("items") is not None))

    out = {
        "ok": True,
        "schema": "openclaw.memory.canonical_health_report.v1",
        "root": str(root),
        "checks": checks,
        "all_green": all(bool(v) for v in checks.values()),
    }
    if write_path:
        p = Path(write_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2), encoding="utf-8")
        out["written"] = str(p)
    return out


def _legacy_readiness_report(root: str, write_path: str | None = None, snapshot: bool = False) -> dict:
    beads_events = Path(root) / ".beads" / "events"
    shim_log = beads_events / "legacy-shim-usage.jsonl"
    trigger_log = beads_events / "write-trigger-processed.jsonl"

    shim_rows = []
    if shim_log.exists():
        for line in shim_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                shim_rows.append(rec)

    trigger_rows = []
    if trigger_log.exists():
        for line in trigger_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                trigger_rows.append(rec)

    trigger_status_counts: dict[str, int] = {}
    for r in trigger_rows:
        st = str(r.get("status") or "unknown")
        trigger_status_counts[st] = trigger_status_counts.get(st, 0) + 1

    shim_count = len(shim_rows)
    legacy_dispatch_count = sum(1 for r in trigger_rows if str(r.get("status") or "") in {"done", "failed", "retired", "ignored"})
    blocked_count = sum(1 for r in trigger_rows if str(r.get("status") or "") == "blocked")

    strict_block_env = str(os.getenv("CORE_MEMORY_BLOCK_LEGACY_TRIGGER_ORCHESTRATOR", "0")).strip().lower() in {"1", "true", "yes", "on"}
    trigger_block_env = str(os.getenv("CORE_MEMORY_ALLOW_LEGACY_WRITE_TRIGGERS", "0")).strip().lower() not in {"1", "true", "yes", "on"}

    ready = (shim_count == 0) and (legacy_dispatch_count == 0)

    out = {
        "ok": True,
        "schema": "openclaw.memory.legacy_readiness_report.v1",
        "root": str(root),
        "ready_for_legacy_removal": bool(ready),
        "strict_block_env": bool(strict_block_env),
        "legacy_write_trigger_blocked_by_default": bool(trigger_block_env),
        "summary": {
            "shim_usage_count": shim_count,
            "legacy_dispatch_count": legacy_dispatch_count,
            "legacy_dispatch_blocked_count": blocked_count,
        },
        "trigger_status_counts": trigger_status_counts,
        "next_actions": [
            "Enable strict shim blocking in CI/staging: CORE_MEMORY_BLOCK_LEGACY_TRIGGER_ORCHESTRATOR=1",
            "Keep CORE_MEMORY_ALLOW_LEGACY_WRITE_TRIGGERS unset in production",
            "Wait for zero shim usage over burn-in window before removing legacy modules",
        ],
    }

    if write_path:
        p = Path(write_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2), encoding="utf-8")
        out["written"] = str(p)

    if snapshot:
        out["snapshot_written"] = _write_legacy_readiness_snapshot(root, out)

    return out


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
    retrieve_ctx_parser.add_argument("--deep-recall", action="store_true", help="Enable bounded deep recall (auto-uncompact compacted/archived beads)")
    retrieve_ctx_parser.add_argument("--max-uncompact-per-turn", type=int, default=2, help="Bounded deep recall budget per call")
    retrieve_ctx_parser.add_argument("--no-auto-memory-intent", action="store_true", help="Disable memory-intent heuristic trigger")
    
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

    # canonical consolidate command (runtime owner)
    consolidate_parser = subparsers.add_parser("consolidate", help="Run canonical runtime consolidation/flush pipeline")
    consolidate_parser.add_argument("--session", required=True, help="Session id")
    consolidate_parser.add_argument("--promote", action="store_true", help="Enable promote mode")
    consolidate_parser.add_argument("--token-budget", type=int, default=1200)
    consolidate_parser.add_argument("--max-beads", type=int, default=12)
    consolidate_parser.add_argument("--source", default="admin_cli")

    # legacy alias (to be removed after transition)
    flush_parser = subparsers.add_parser("flush", help="[Deprecated alias] Use 'consolidate'")
    flush_parser.add_argument("--session", required=True, help="Session id")
    flush_parser.add_argument("--promote", action="store_true", help="Enable promote mode")
    flush_parser.add_argument("--token-budget", type=int, default=1200)
    flush_parser.add_argument("--max-beads", type=int, default=12)
    flush_parser.add_argument("--source", default="admin_cli")

    # rolling-window refresh command
    rw_parser = subparsers.add_parser("rolling-window", help="Run rolling window maintenance pipeline")
    rw_parser.add_argument("--token-budget", type=int, default=1200)
    rw_parser.add_argument("--max-beads", type=int, default=12)

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

    # openclaw integration onboarding
    oc_parser = subparsers.add_parser("openclaw", help="OpenClaw integration onboarding + diagnostics")
    oc_sub = oc_parser.add_subparsers(dest="openclaw_cmd")
    oc_onboard = oc_sub.add_parser("onboard", help="Install/enable Core Memory bridge plugin in OpenClaw")
    oc_onboard.add_argument("--openclaw-bin", default="openclaw")
    oc_onboard.add_argument("--plugin-dir", help="Path to core-memory bridge plugin directory")
    oc_onboard.add_argument("--replace-memory-core", action="store_true", help="Disable stock memory-core plugin")
    oc_onboard.add_argument("--dry-run", action="store_true")

    # reason command
    reason_parser = subparsers.add_parser("reason", help="Reasoned memory recall (semantic + causal)")
    reason_parser.add_argument("query", help="Natural language query")
    reason_parser.add_argument("--k", type=int, default=8)
    reason_parser.add_argument("--retrieve", action="store_true", help="Return retrieval output mode")
    reason_parser.add_argument("--debug", action="store_true", help="Include retrieval scoring breakdown")
    reason_parser.add_argument("--explain", action="store_true", help="Write deterministic explain report artifact")

    tag_parser = subparsers.add_parser("tag", help="Tag beads with metadata")
    tag_parser.add_argument("--incident", help="Incident ID")
    tag_parser.add_argument("--topic-key", help="Topic key tag")
    tag_parser.add_argument("bead_ids", nargs="+", help="Bead IDs to update")

    hygiene_parser = subparsers.add_parser("hygiene", help="Curated metadata hygiene tools")
    hygiene_parser.add_argument("--bead-id", action="append", help="Target bead id (repeatable)")
    hygiene_parser.add_argument("--bead-ids-file", help="Path to JSON array of bead IDs")
    hygiene_parser.add_argument("--apply", action="store_true")

    mem_parser = subparsers.add_parser("memory", help="Typed memory-search skill interface")
    mem_sub = mem_parser.add_subparsers(dest="memory_cmd")
    mem_sub.add_parser("form", help="Get machine-readable search form + catalog")
    mem_search = mem_sub.add_parser("search", help="Run typed memory search")
    mem_search.add_argument("--typed", required=True, help="JSON object string or path to JSON file")
    mem_search.add_argument("--explain", action="store_true")
    mem_exec = mem_sub.add_parser("execute", help="Run unified MemoryRequest execution")
    mem_exec.add_argument("--request", required=True, help="JSON object string or path to JSON file")
    mem_exec.add_argument("--explain", action="store_true")

    # graph command
    graph_parser = subparsers.add_parser("graph", help="Graph build/stats tools")
    graph_sub = graph_parser.add_subparsers(dest="graph_cmd")
    graph_sub.add_parser("build", help="Backfill structural edges and rebuild graph snapshot")
    graph_sub.add_parser("stats", help="Show graph edge/node stats")
    graph_sub.add_parser("decay", help="Run semantic edge decay pass")
    g_sem_build = graph_sub.add_parser("semantic-build", help="Build semantic lookup index")
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

    metrics_legacy = metrics_sub.add_parser("legacy-readiness", help="Report legacy-path closure readiness")
    metrics_legacy.add_argument("--write", help="Optional JSON output path")
    metrics_legacy.add_argument("--snapshot", action="store_true", help="Write dated JSON+MD readiness snapshot under docs/reports/")

    metrics_canonical = metrics_sub.add_parser("canonical-health", help="Run canonical contract health checks")
    metrics_canonical.add_argument("--write", help="Optional JSON output path")
    
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
            deep_recall=args.deep_recall,
            max_uncompact_per_turn=args.max_uncompact_per_turn,
            auto_memory_intent=not args.no_auto_memory_intent,
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

    elif args.command in {"flush", "consolidate"}:
        if args.command == "flush":
            print("[deprecated] 'flush' is an alias; use 'consolidate'", file=sys.stderr)
        result = process_flush(
            root=str(memory.root),
            session_id=args.session,
            promote=bool(args.promote),
            token_budget=int(args.token_budget),
            max_beads=int(args.max_beads),
            source=str(args.source or "admin_cli"),
        )
        print(json.dumps(result, indent=2))

    elif args.command == "rolling-window":
        result = run_rolling_window_pipeline(
            token_budget=int(args.token_budget),
            max_beads=int(args.max_beads),
        )
        print(json.dumps(result, indent=2))

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

    elif args.command == "openclaw":
        if args.openclaw_cmd == "onboard":
            out = run_openclaw_onboard(
                openclaw_bin=args.openclaw_bin,
                plugin_dir=args.plugin_dir,
                replace_memory_core=bool(args.replace_memory_core),
                dry_run=bool(args.dry_run),
            )
            print(render_onboard_report(out))
            if not out.get("ok"):
                raise SystemExit(2)
        else:
            oc_parser.print_help()

    elif args.command == "reason":
        out = memory_reason(
            args.query,
            k=args.k,
            root=str(memory.root),
            debug=bool(args.debug or args.retrieve or args.explain),
            explain=bool(args.explain),
        )
        print(json.dumps(out, indent=2))

    elif args.command == "tag":
        if bool(args.incident) == bool(args.topic_key):
            raise SystemExit("tag requires exactly one of --incident or --topic-key")
        if args.incident:
            print(json.dumps(tag_incident(memory.root, incident_id=args.incident, bead_ids=args.bead_ids), indent=2))
        else:
            print(json.dumps(tag_topic_key(memory.root, topic_key=args.topic_key, bead_ids=args.bead_ids), indent=2))

    elif args.command == "hygiene":
        target_ids = list(args.bead_id or [])
        if args.bead_ids_file:
            payload = json.loads(Path(args.bead_ids_file).read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise SystemExit("--bead-ids-file must contain a JSON array")
            target_ids.extend([str(x) for x in payload])
        print(json.dumps(curated_type_title_hygiene(memory.root, target_ids, apply=args.apply), indent=2))

    elif args.command == "memory":
        if args.memory_cmd == "form":
            print(json.dumps(memory_get_search_form(str(memory.root)), indent=2))
        elif args.memory_cmd == "search":
            typed = str(args.typed or "")
            if typed.strip().startswith("{"):
                payload = json.loads(typed)
            else:
                payload = json.loads(Path(typed).read_text(encoding="utf-8"))
            print(json.dumps(memory_search_typed(str(memory.root), payload, explain=bool(args.explain)), indent=2))
        elif args.memory_cmd == "execute":
            req = str(args.request or "")
            if req.strip().startswith("{"):
                payload = json.loads(req)
            else:
                payload = json.loads(Path(req).read_text(encoding="utf-8"))
            print(json.dumps(memory_execute(str(memory.root), payload, explain=bool(args.explain)), indent=2))
        else:
            mem_parser.print_help()

    elif args.command == "graph":
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
            print(json.dumps(backfill_causal_links(
                memory.root,
                apply=args.apply,
                max_per_target=args.max_per_target,
                min_overlap=args.min_overlap,
                require_shared_turn=not bool(args.no_require_shared_turn),
                include_bead_ids=target_ids,
            ), indent=2))
        else:
            graph_parser.print_help()

    elif args.command == "metrics":
        if args.metrics_cmd == "report":
            print(json.dumps(memory.metrics_report(since=args.since), indent=2))
        elif args.metrics_cmd == "schema-quality":
            print(json.dumps(memory.schema_quality_report(write_path=args.write), indent=2))
        elif args.metrics_cmd == "rebalance-promotions":
            print(json.dumps(memory.rebalance_promotions(apply=args.apply), indent=2))
        elif args.metrics_cmd == "promotion-slate":
            print(json.dumps(memory.promotion_slate(limit=args.limit, query_text=args.query), indent=2))
        elif args.metrics_cmd == "decide-promotion":
            print(json.dumps(memory.decide_promotion(
                bead_id=args.id,
                decision=args.decision,
                reason=args.reason,
                considerations=args.consideration or [],
            ), indent=2))
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
        elif args.metrics_cmd == "legacy-readiness":
            print(json.dumps(_legacy_readiness_report(str(memory.root), write_path=args.write, snapshot=bool(args.snapshot)), indent=2))
        elif args.metrics_cmd == "canonical-health":
            print(json.dumps(_canonical_health_report(str(memory.root), write_path=args.write), indent=2))
        else:
            metrics_parser.print_help()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
