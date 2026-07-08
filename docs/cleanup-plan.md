# Core Memory Cleanup Plan

**Created:** 2026-05-24
**Workstream:** Code hygiene + storage adapter boundary

This document tracks the cleanup and architectural improvement workstream identified from
codebase analysis. Each phase is a discrete, mergeable unit with its own test gate.
Do not start a phase until the previous one has passed CI.

PRDs for all phases live in `docs/PRD/` and carry codebase-specific implementation detail.

---

## Phase 0 — CI + Coverage Baseline

**Goal:** Make regressions visible before touching anything.

- [x] Add `.github/workflows/test.yml` — runs `pytest tests/` on push/PR, fails on red,
      installs only required (non-extra) deps so accidental hard-import-of-extra is caught
- [x] Add a second job in `test.yml` that installs `core-memory[all]` and runs the full suite
- [x] Add `pytest-cov` and publish coverage as a step artifact (no floor gate yet; just
      establish baseline)
- [x] Tag integration tests that exercise the things Phases 4–5 touch:
      `pytest.mark.facade` for tests that import from `core_memory.graph.api`,
      `pytest.mark.mixin_assembly` for tests that instantiate `MemoryStore` end-to-end
- [x] Add `.github/workflows/architecture-guards.yml` — runs the architecture
      guard and focused guard tests for code, current docs, guard script, and
      guard baseline changes

**Risk:** None. Additive CI only.

---

## Phase 1 — Classify Retained Dead-File Candidates

**Goal:** Classify the retained files that were previously described as deleted.
Do not delete them from this phase's historical checklist without a fresh
public-surface/import check.

**Correction:** The original list contained 4 files. `core_memory/retrieval/vector_backend.py`
is **NOT dead** — it is imported by `core_memory/retrieval/semantic_index.py`. Do not delete it.

**Truth-audit correction (2026-06-28, updated 2026-07-07):** These paths were
previously overstated as deleted. The retained candidate is now classified in
`docs/compatibility_ledger.md`; the retired candidates were removed only after
their proof gates passed.

**PRD:** `docs/PRD/01-dead-file-removal.md`

- [x] `core_memory/persistence/encryption.py` — classified as public optional
      compatibility and covered by `tests/test_persistence_encryption_compat.py`
- [x] `core_memory/persistence/write_ops.py` — retired after import scan
- [x] `core_memory/retrieval/pipeline/explain.py` — retired after import scan

**Risk:** None for this truth correction. Any future deletion requires a fresh
import scan, public-surface check, and relevant tests.

---

## Phase 2 — Fix Mislabeled Circular-Import Workarounds

**Goal:** Remove dead defensive code; correct misleading docstrings.

**PRD:** `docs/PRD/02-circular-import-fix.md`

- [x] `core_memory/retrieval/__init__.py` — **Resolved in Phase 9g.** The real
      cycle (`retrieval/pipeline/canonical.py` → `integrations/api.py` →
      `runtime/queue/jobs.py` → `retrieval/lifecycle.py`) was broken by
      converting the top-level `hydrate_bead_sources` import in `canonical.py`
      to lazy function-level imports. The `__getattr__` hook is retained as
      documented defense.
- [x] `core_memory/runtime/__init__.py` — docstring rewritten to describe the
      lazy-load rationale accurately (no "circular import" claim).

**Risk:** Near zero. The completed change is docstring-only.

---

## Phase 3A — Harden the PydanticAI Boundary

**Goal:** Lock in that `pydantic-ai` is optional; prevent it silently becoming required.

**PRD:** `docs/PRD/03a-pydanticai-boundary.md`

- [x] Add `pytest.mark.skipif` guards to `tests/test_pydanticai_adapter.py` and
      `tests/test_pydanticai_memory_tools.py` *(done in Phase 0)*
- [x] Add CI matrix entry (`core-only` job) that installs without `[pydanticai]` *(done in Phase 0)*
- [x] Add `tests/test_adapter_boundary_pydanticai.py` — subprocess-isolated assertion
      that `pydantic_ai` never appears in `sys.modules` via `import core_memory` or
      any internal subpackage

**Risk:** Low. Additive guards only.

---

## Phase 4 — Classify `graph/api.py` Compat Facade

**PRD:** `docs/PRD/04-graph-module-cleanup.md`

**Truth-audit correction (2026-06-28, updated 2026-07-07):**
`core_memory/graph/api.py` still exists and is classified in
`docs/compatibility_ledger.md` as a public compatibility facade. It is
classify-not-delete unless a future deprecation/removal window satisfies the
ledger condition.

- [x] Signature check: CLI already passed `anchor_ids=` as keyword; no fix needed
- [x] Rename `_api_impl.py` → `core.py`; update all internal references
- [x] Migrate `core_memory/cli_handlers_graph.py` to import from split modules
- [x] Migrate all 12 test files that imported from `core_memory.graph.api`
- [x] Update `core_memory/graph/__init__.py`: explicit re-exports from split modules
- [x] Classify `core_memory/graph/api.py` in `docs/compatibility_ledger.md`;
      retain until the ledger's deprecation/removal condition is satisfied

**Risk:** Medium. Many touched files but mechanical. Run Phase 0 CI after each step.

---

## Phase 5 — Flatten Persistence Delegation Chain

**PRD:** `docs/PRD/05-persistence-delegation-flatten.md`

- [x] **Step 5a — Audit:** Write a script that inspects each `*_for_store` function and
      classifies it as STATEFUL (reads/writes `store` fields), STATELESS (ignores `store`
      param), or PARTIAL. Finding: 0 STATELESS, 36 STATEFUL, 12 PARTIAL (48 total).
      Report: `docs/reports/store-delegation-audit-2026-05-26.md`
- [x] **Step 5b — Pilot:** `store_text_hygiene_ops.py` — remove `store` param from
      STATELESS functions, update mixin to call directly, run full suite
- [x] **Step 5c — Remaining files:** PARTIAL pass-through wrappers removed.
      Down from 12 → 3 PARTIAL (39 total `*_for_store` functions). Flattened in
      per-file commits:
      - `store_promotion_ops.py` — 7 `*_entry_for_store` pass-throughs removed; mixin now
        imports from `promotion_service` directly
      - `store_lifecycle_ops.py` — `safe_del_for_store` inlined into `MemoryStore.__del__`
      - `store_dream_bootstrap_ops.py` — `dream_for_store` inlined into
        `StoreCoreDelegatesMixin.dream`

      The 3 remaining PARTIAL functions have real logic and were left as-is per the PRD
      "leave as-is and document why" option:
      - `decide_promotion_bulk_for_store` — bulk-apply loop over `decide_promotion_for_store`
      - `detect_decision_conflicts_for_store` — conflict detection; `store` used for tokenization
      - `check_plan_constraints_for_store` — advisory check; reads via `active_constraints_for_store`

      Other ops files in the original Step 5c list (`store_compaction_ops.py`,
      `store_index_heads_ops.py`, `store_session_ops.py`, `store_autonomy_ops.py`,
      `store_failure_ops.py`, `store_relationship_ops.py`) contain only STATEFUL functions
      with genuine `store.attr` access — no flattening opportunity.
- [x] **Step 5d — Mixin consolidation:** MemoryStore MRO is flat and all 79 methods
      (61 core + 18 reporting/promotion) were inlined directly into `MemoryStore` in
      `store.py`. The legacy mixin artifacts
      `core_memory/persistence/store_core_delegates_mixin.py` and
      `core_memory/persistence/store_reporting_promotion_mixin.py` were later
      retired after import scans proved no active first-party caller depended on
      them. Mixin-assembly tests remain as method-contract assertions on
      `MemoryStore`. Full suite at the time of the original flattening: 1025
      passed, 4 failed (unchanged baseline).

**Risk:** High individually, low per-step. Do not batch steps. Full CI between each file.

---

## Phase 6 — Unify StorageBackend + VectorBackend into Capability Tiers

**PRD:** `docs/PRD/06-storage-adapter-boundary.md`
**Status:** Complete (already implemented)

- [x] Extend `StorageBackend` protocol (`persistence/backend.py`) with three new methods:
      `search_candidates(query_vec, filters, limit)`,
      `traverse(seed_ids, edge_types, max_hops)`,
      `hydrate_turn_refs(turn_refs)`
- [x] Add `BackendCapabilities` dataclass with boolean flags:
      `vector_search`, `graph_traversal`, `full_text_search`, `transcript_hydration`
- [x] `JsonFileBackend` and `SqliteBackend` declare all flags `False`; Python fallbacks fire
- [x] Retrieval pipeline (`canonical.py`) checks `_caps.vector_search` and
      `_caps.graph_traversal` before delegating — else branches are the existing paths
- [x] Existing `VectorBackend` (FAISS/pgvector) remains the fallback for `vector_search=False`
- [x] `tests/test_backend_capabilities.py` — 11 tests covering all criteria (all pass)

**Risk:** Medium. No behavior changes; capability flags start `False` everywhere.

---

## Phase 7 — Graph Backend Abstraction (Pluggable Causal Graph Providers)

**PRD:** `docs/PRD/07-neo4j-query-backend.md`
**Status:** Complete through 7i; live provider tests remain env-gated

### Sub-phase 7a — `persistence/graph/` package + protocol + factory

- [x] `GraphBackend` protocol in `persistence/graph/protocol.py`
- [x] `NullGraphBackend` (explicit `CORE_MEMORY_GRAPH_BACKEND=none`; all-False caps,
      no-op write hooks)
- [x] `KuzuGraphBackend` is the embedded default when `CORE_MEMORY_GRAPH_BACKEND` is unset
- [x] `create_graph_backend(root)` factory reads `CORE_MEMORY_GRAPH_BACKEND`
- [x] `register_graph_backend(name, factory)` plugin hook added to factory
- [x] Factory falls back to `NullGraphBackend` on unknown provider or construction error (no raise)
- [x] Retrieval pipeline (`canonical.py`) routes `graph_traversal` branch through factory
- [x] `test_graph_backend_protocol.py` — 10 tests covering NullGraphBackend contract
- [x] `test_graph_backend_factory.py` — env routing, missing-dep fallback, plugin registry

### Sub-phase 7b — `Neo4jGraphBackend` read path

- [x] `traverse()` via Cypher variable-length path query
- [x] `health()` liveness probe (`RETURN 1`)
- [x] `capabilities()` with TTL-based health probe — returns all-False when Neo4j unreachable
- [x] `KuzuGraphBackend` (embedded; `CORE_MEMORY_GRAPH_BACKEND=kuzu`) — same interface
- [x] `test_graph_backend_capabilities.py` — 5 tests (healthy/unhealthy/TTL/recovery)
- [x] `test_graph_backend_neo4j_parity.py` + `test_kuzu_graph_backend.py` — mocked coverage
- [x] Live tests are env-gated (`@pytest.mark.neo4j` and related provider marks);
      always-on CI relies on mocked/fake/local embedded coverage.

### Sub-phase 7c — Write-side hooks

- [x] `store_add_bead_ops.py` calls `graph.on_bead_written(bead)` after local write
- [x] `store_relationship_ops.py` calls `graph.on_association_written(assoc)` after link write
- [x] Failures are logged as warnings; never block the local write

### Sub-phase 7d — `core-memory graph backend-sync` CLI

- [x] `graph backend-sync [--dry-run]` subcommand added to parser + handler
- [x] Loads beads/associations from `StorageBackend`, calls `gb.sync_from_storage(beads, assocs)`
- [x] Returns exit code 2 when no graph backend is configured

### Sub-phases 7e–7i (complete)

- [x] `GraphitiGraphBackend` — self-hosted (7e) + Zep-hosted alias (7f) + LLM client injection protocol (7g)
- [x] `ObsidianSyncTarget` (BeadSyncTarget protocol) — markdown vault write + Local REST search (7h)
- [x] Plugin API docs — `docs/graph_backend_plugin.md` (7i)

**Risk:** Medium. Isolated to `persistence/graph/`. No changes to core recall path.

---

## Phase 8 — `core-memory init` Guided Wizard + `core-memory doctor` Expansion

**PRD:** `docs/PRD/08-init-wizard.md`

### Phase 8a — Complete (2026-05-26)

- [x] Layered config reader (`core_memory/config/settings.py`):
      defaults < user-global `~/.core-memory/config.yaml` < project-local `.core-memory.yaml` < env vars
- [x] `core-memory setup init` guided wizard with `--preset`, `--global`, `--force`;
      creates `.beads/` + `.turns/`, writes `.core-memory.yaml`; idempotent without `--force`
- [x] `core-memory setup doctor` expanded to 6 capability tiers: storage, vector search,
      graph traversal, transcript hydration, dreamer, rolling window — structured JSON output,
      exits 1 on any error tier

### Phase 8b — Complete (2026-05-26)

- [x] **8b-1 Mode-based wizard** — `--mode local|mcp|app|production` replaces `--preset`;
      first wizard question is use-case intent; `--preset` kept as deprecated alias; `mode`
      field written to config; Kuzu is the default graph for all non-production modes
- [x] **8b-2 Doctor profiles** — `--profile` auto-detected from config `mode`; severity
      matrix gates what's error/warning/info/hidden per profile; human-readable default output
      with ✓/⚠/✗/ℹ icons and three-part Impact/Fix hints; auto-JSON when stdout is not a
      tty; Kuzu shown as "embedded, zero-config" for local/mcp/app, never a warning
- [x] **8b-3 `core-memory config` subcommand** — `config show` with per-key provenance
      (file path / "default" / "env:VAR"); `config set key value` non-destructive in-place
      update; `config validate` catches neo4j-without-uri, postgres-without-dsn, and
      production-without-durable-backend contradictions
- [x] **8b-4 `core-memory demo`** — writes 3 synthetic beads, queries, prints result + graph
      status; cleans up session file on exit; `--keep` retains beads

**Risk:** Low for wizard/config/demo (additive). Medium for doctor refactor (touches probe
logic and human output format).

---

---

## Phase 9 — Structural Consolidation

**PRD:** `docs/PRD/09-structural-consolidation.md`

- [x] **9a — Extract generic feature flags** from `integrations/openclaw_flags.py` into
      `core_memory/config/feature_flags.py`. Only `supersede_openclaw_summary_enabled()`
      stays with OpenClaw. Update all importers in `runtime/` and `integrations/api.py`.
- [x] **9b — Rename event schema strings** — replace `"openclaw.memory.*"` string
      literals in `engine.py` and `flush_flow.py` with constants now canonical in
      `schema/event_schemas.py`; `runtime/event_schemas.py` remains a compatibility
      import path. Accept legacy values on read during transition.
- [x] **9c — Move OpenClaw files into `integrations/openclaw/`** — 7 flat files become a
      proper subdirectory matching every other integration. Backward-compat re-export
      shims left at old `integrations/openclaw_*.py` paths.
- [x] **9d — Move CLI into `core_memory/cli/`** — `cli.py` → `cli/__init__.py` + `__main__.py`,
      `cli/parsers/` and `cli/handlers/` subdirectories hold all 15 CLI files.
      Entry point `core_memory.cli:main` works without `pyproject.toml` changes.
      Backward-compat shims left at all old `cli_*.py` flat paths.
- [x] **9e — Move Dreamer to `runtime/dreamer/`** — `dreamer.py` (top level),
      `runtime/dreamer_candidates.py`, `runtime/dreamer_eval.py`, and
      `runtime/longitudinal_benchmark.py` all move to
      `runtime/dreamer/analysis.py`, `candidates.py`, `eval.py`, `longitudinal.py`.
      Backward-compat shims left at all four old paths.
- [x] **9f — Reorganize `runtime/` into subdirectories** — create `turn/`, `flush/`,
      `session/`, `passes/`, `queue/`, `observability/` subdirectories. Each `__init__.py`
      re-exports old module symbols as a migration shim.
- [x] **9g — Thin `integrations/api.py`** — audit all 22+ outgoing imports; any that
      reach internal modules (`runtime.*`, `claim.*`, etc.) are replaced with imports
      from `core_memory`'s public `__all__`. Gaps in the public API are filled in
      `__init__.py` first.

      > **Layering violation fixed.** The cycle `retrieval/pipeline/canonical.py →
      > integrations/api → runtime/jobs → retrieval` was broken by converting the
      > module-level `from core_memory.integrations.api import hydrate_bead_sources`
      > in `canonical.py` to lazy function-level imports (two call sites).
      > `hydrate_bead_sources` added to `core_memory/__init__.py` public API.
      > `retrieval/__init__.py` `__getattr__` guard preserved and documented.
      >
      > Remaining: `api.py` internal imports (`runtime.*`, `claim.*`, `entity.*`) are
      > implementation details of integration functions, not layering violations in the
      > context of the integrations/ tier. Full replacement would require either
      > expanding the public API with internal utilities or restructuring api.py into
      > a thinner dispatch layer. That remains future integration API cleanup, not
      > Phase 10 documentation consolidation.
- [x] **9h — Classify remaining backward-compat shims** — most re-export shims
      created during Phase 9 structural moves were removed, but this historical
      checklist must not be treated as proof that every listed flat file is gone.
      Full inventory:

      > **Truth-audit correction (2026-06-28, updated 2026-06-29):** Some flat
      > compatibility/relocation files still exist in the current tree. Treat
      > this section as a migration checklist, not deletion proof. The former
      > `core_memory/cli_handlers_semantic.py` root file has since been retired
      > after the semantic CLI package handler was proven canonical.

      **From 9a** (1 shim — deleted ✓):
      - ~~`integrations/openclaw_flags.py`~~

      **From 9e** (4 shims — all deleted ✓):
      - ~~`dreamer.py`~~, ~~`runtime/dreamer_candidates.py`~~, ~~`runtime/dreamer_eval.py`~~, ~~`runtime/longitudinal_benchmark.py`~~

      **From 9c** (7 shims — all deleted ✓):
      - ~~`integrations/openclaw_agent_end_bridge.py`~~, ~~`compaction_bridge.py`~~,
        ~~`compaction_queue.py`~~, ~~`onboard.py`~~, ~~`read_bridge.py`~~, ~~`runtime.py`~~,
        ~~`openclaw_flags.py`~~

      **From 9d** (15 shims — mostly migrated; retained debt remains):
      - Most `cli_*.py` flat files migrated to the `core_memory/cli/` package.
        `core_memory/cli_handlers_semantic.py` has now been retired; the live
        handler remains `core_memory/cli/handlers/semantic.py`.

      **From 9f** (30 shims — mostly migrated; retained debt may remain):
      - Most `runtime/` root shims migrated to canonical subpackage paths under
        `runtime/{turn,flush,session,passes,queue,observability}/`. Verify the
        current tree before claiming any root file is deleted.
      - Historical flat-file migration candidates included `cli_compat.py`,
        `cli_diagnostics.py`, `cli_handlers_graph.py`,
        `cli_handlers_integrations.py`, `cli_handlers_metrics.py`,
        `cli_handlers_migrate.py`, `cli_handlers_ops.py`, `cli_handlers_semantic.py`,
        `cli_handlers_setup.py`, `cli_handlers_store.py`, `cli_memory_handlers.py`,
        `cli_parser_extended.py`, `cli_parser_memory.py`, `cli_parser_ops.py`

      **From 9f** (30 shims — mostly migrated; verify before deleting):
      Most `runtime/` root shims migrated to canonical subpackage paths. Verify current
      tree state before claiming any remaining root file is deleted.

      **Prerequisite:** grep the full repo (tests, docs, external tool scripts) for any
      import of each old flat path. Update each callsite to the canonical location before
      deleting. This task should be done as one PR per shim group (9a, 9c, 9d, 9e, 9f) so
      failures are easy to bisect.

**Risk:** Medium per sub-task, high in aggregate. Do one sub-task per PR. Never batch.

---

## Phase 10 — Documentation Consolidation

**PRD:** `docs/PRD/10-documentation-consolidation.md`

- [x] **10a — Archive 11 stray `v2_p*` files** from `docs/` root to
      `docs/archive/history/` (they belong there with the rest of the phase history)
- [x] **10b — Retire `docs/ARCHITECTURE.md`** — it references pre-v2 file names; merge
      any still-accurate content into `architecture_overview.md`, then archive it
- [x] **10c — Update `architecture_overview.md`** to reflect post-Phase-9 directory
      structure (new runtime subdirs, cli/ package, integrations/openclaw/, capability
      tiers, init wizard, graph backend tier). Target: 100–150 lines, readable in 5 minutes.
- [x] **10d — Audit and classify all docs/ root files** (30+ files) as Current, Snapshot,
      or Superseded. Move Snapshot/Superseded files to `docs/reports/` or `docs/archive/`.
      Every Current file gets verified and listed in `docs/index.md`.
- [x] **10e — Create `docs/status.md`** — single tracked-state document merging
      `demo/TODO.md`'s correctness items (#1–#9), the cleanup workstream (phases 0–10),
      and current completion states.
- [x] **10f — Add `docs/PRD/README.md`** — index of all PRD files with one-line
      descriptions and statuses.
- [x] **10g — Update `docs/index.md`** — fix broken links from Phase 9 file moves,
      add PRD section, add open-workstreams section, fix Neo4j label, add Graphiti/
      Obsidian entries, add `status.md` link.

**Risk:** None functionally. Each sub-task is a docs-only PR.

---

## Sequence dependency

```
Phase 0 (CI) → must precede everything
Phase 1, 2, 3A → independent, can run in any order after Phase 0
Phase 4 → after Phase 0
Phase 5 → after Phase 4 (cleaner store surface makes audit more meaningful)
Phase 6 → after Phase 5 (flat ops surface makes protocol extension cleaner)
Phase 7 → after Phase 6 (needs extended StorageBackend protocol)
Phase 8 → after Phase 6 (needs BackendCapabilities + create_backend config support)
Phase 9 → after Phase 4 and 5 (structural moves are smaller when Phase 4/5 are done first)
Phase 10 → after Phase 9 (architecture docs must reflect actual post-refactor layout)
```

---

## What this workstream does NOT change

- Public API: `Memory`, `MemorySession`, `recall()`, HTTP routes, MCP tools
- Bead schema, causal edge types, supersession logic, rolling window semantics
- On-disk storage format (JSONL session files remain the source of truth)
- Existing TODO items #1–#7 in `demo/TODO.md` (separate correctness workstream)
