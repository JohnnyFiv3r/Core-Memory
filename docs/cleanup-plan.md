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

- [ ] Add `.github/workflows/test.yml` — runs `pytest tests/` on push/PR, fails on red,
      installs only required (non-extra) deps so accidental hard-import-of-extra is caught
- [ ] Add a second job in `test.yml` that installs `core-memory[all]` and runs the full suite
- [ ] Add `pytest-cov` and publish coverage as a step artifact (no floor gate yet; just
      establish baseline)
- [ ] Tag integration tests that exercise the things Phases 4–5 touch:
      `pytest.mark.facade` for tests that import from `core_memory.graph.api`,
      `pytest.mark.mixin_assembly` for tests that instantiate `MemoryStore` end-to-end

**Risk:** None. Additive CI only.

---

## Phase 1 — Delete Confirmed Dead Files

**Goal:** Remove the 3 files with zero references anywhere in the repo.

**Correction:** The original list contained 4 files. `core_memory/retrieval/vector_backend.py`
is **NOT dead** — it is imported by `core_memory/retrieval/semantic_index.py`. Do not delete it.

**PRD:** `docs/PRD/01-dead-file-removal.md`

- [x] `core_memory/persistence/encryption.py` — stub with env-var docs, never imported
- [x] `core_memory/persistence/write_ops.py` — delegating stub, never imported
- [x] `core_memory/retrieval/pipeline/explain.py` — defines `build_explain()`, never called

**Risk:** None if Phase 0 CI is in place.

---

## Phase 2 — Fix Mislabeled Circular-Import Workarounds

**Goal:** Remove dead defensive code; correct misleading docstrings.

**PRD:** `docs/PRD/02-circular-import-fix.md`

- [ ] `core_memory/retrieval/__init__.py` — **DEFERRED to Phase 9g.** A real cycle
      was found: `retrieval/pipeline/canonical.py` → `integrations/api.py` →
      `runtime/jobs.py` → `runtime/engine.py` (layering violation). The `__getattr__`
      hook is live defense, not dead code. Fix the layering violation first.
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

## Phase 4 — Remove `graph/api.py` Compat Facade

**PRD:** `docs/PRD/04-graph-module-cleanup.md`

- [x] Signature check: CLI already passed `anchor_ids=` as keyword; no fix needed
- [x] Rename `_api_impl.py` → `core.py`; update all internal references
- [x] Migrate `core_memory/cli_handlers_graph.py` to import from split modules
- [x] Migrate all 12 test files that imported from `core_memory.graph.api`
- [x] Update `core_memory/graph/__init__.py`: explicit re-exports from split modules
- [x] Delete `core_memory/graph/api.py`

**Risk:** Medium. Many touched files but mechanical. Run Phase 0 CI after each step.

---

## Phase 5 — Flatten Persistence Delegation Chain

**PRD:** `docs/PRD/05-persistence-delegation-flatten.md`

- [ ] **Step 5a — Audit:** Write a script that inspects each `*_for_store` function and
      classifies it as STATEFUL (reads/writes `store` fields), STATELESS (ignores `store`
      param), or PARTIAL. Expected finding: <10% are truly stateful.
- [ ] **Step 5b — Pilot:** `store_text_hygiene_ops.py` — remove `store` param from
      STATELESS functions, update mixin to call directly, run full suite
- [ ] **Step 5c — Remaining files** (one per PR, smallest to largest):
      `store_compaction_ops.py`, `store_dream_bootstrap_ops.py`, `store_index_heads_ops.py`,
      `store_session_ops.py`, `store_autonomy_ops.py`, `store_failure_ops.py`,
      `store_lifecycle_ops.py`, `store_promotion_ops.py`, `store_relationship_ops.py`
- [ ] **Step 5d — Mixin consolidation:** Once ops files are flat, evaluate whether
      `StoreCoreDelegatesMixin` (61 methods) and `StoreReportingPromotionMixin` (18 methods)
      should be inlined into `MemoryStore` or kept as thinner mixins

**Risk:** High individually, low per-step. Do not batch steps. Full CI between each file.

---

## Phase 6 — Unify StorageBackend + VectorBackend into Capability Tiers

**PRD:** `docs/PRD/06-storage-adapter-boundary.md`

- [ ] Extend `StorageBackend` protocol (`persistence/backend.py`) with three new methods:
      `search_candidates(query_vec, filters, limit)`,
      `traverse(seed_ids, edge_types, max_hops)`,
      `hydrate_turn_refs(turn_refs)`
- [ ] Add `BackendCapabilities` dataclass with boolean flags:
      `vector_search`, `graph_traversal`, `full_text_search`, `transcript_hydration`
- [ ] `JsonFileBackend` and `SqliteBackend` declare `vector_search=False,
      graph_traversal=False` — Python fallbacks handle those tiers
- [ ] Wire retrieval pipeline to check capabilities before delegating: use backend method
      if `capabilities.vector_search` else use existing FAISS/pgvector path
- [ ] Keep existing `VectorBackend` abstraction working as a side-car for backends that
      declare `vector_search=False`

**Risk:** Medium. No behavior changes; capability flags start `False` everywhere.

---

## Phase 7 — Promote Neo4j from Sync Target to Query Backend

**PRD:** `docs/PRD/07-neo4j-query-backend.md`

- [ ] Create `integrations/neo4j/backend.py` implementing the extended `StorageBackend`
      protocol with `capabilities.graph_traversal = True`
- [ ] Implement `traverse()` via Cypher: `MATCH (b:Bead {id: $id})-[r*1..3]->(n:Bead)`
      using existing `client.py` and `mapper.py`
- [ ] `backend.py` is the read path; existing `sync.py` remains the write path
- [ ] Register via `create_backend(root, backend="neo4j")`
- [ ] Add `neo4j` backend option to `CORE_MEMORY_BACKEND` env var docs
- [ ] Add integration tests that run against a local Neo4j instance (Docker Compose or
      pytest-docker) and cover `traverse()` + `search_candidates()` round-trips

**Risk:** Medium. Isolated to Neo4j integration module. No changes to core recall path.

---

## Phase 8 — `core-memory init` Guided Wizard + `core-memory doctor` Expansion

**PRD:** `docs/PRD/08-init-wizard.md`

- [ ] Expand `core-memory init` into a guided wizard with `--preset` flag for
      non-interactive use. Wizard options:
      Install type (local/sqlite/postgres/neo4j/custom),
      Runtime integration (MCP/OpenClaw/PydanticAI/HTTP/none),
      Memory behavior (rolling window size, dreamer on/off, grounding on/off)
- [ ] Write output to `~/.core-memory/config.yaml` (user-global) or `.core-memory.yaml`
      (project-local, takes precedence); `create_backend()` reads this file
- [ ] Expand `core-memory doctor` to verify each capability tier:
      storage backend reachable, vector search working, graph traversal working (if
      applicable), transcript hydration, dreamer status
- [ ] Make Neo4j the documented recommended quick-install for users who want native
      causal traversal; keep local JSONL as the no-deps dev path

**Risk:** Low for wizard (additive). Medium for doctor (touches diagnostic paths).

---

---

## Phase 9 — Structural Consolidation

**PRD:** `docs/PRD/09-structural-consolidation.md`

- [ ] **9a — Extract generic feature flags** from `integrations/openclaw_flags.py` into
      `core_memory/config/feature_flags.py`. Only `supersede_openclaw_summary_enabled()`
      stays with OpenClaw. Update all importers in `runtime/` and `integrations/api.py`.
- [ ] **9b — Rename event schema strings** — replace `"openclaw.memory.*"` string
      literals in `engine.py` and `flush_flow.py` with constants from a new
      `runtime/event_schemas.py`. Accept legacy values on read during transition.
- [ ] **9c — Move OpenClaw files into `integrations/openclaw/`** — 7 flat files become a
      proper subdirectory matching every other integration. Add backward-compat re-export
      shims at old paths for one release cycle. Extract abstract `CrawlerContract`
      protocol in `association/` so it no longer imports OpenClaw by name.
- [ ] **9d — Move CLI into `core_memory/cli/`** — rename `cli.py` to `cli/__init__.py`,
      create `cli/parsers/` and `cli/handlers/` subdirectories, move all 13 CLI files.
      Entry point `core_memory.cli:main` works without `pyproject.toml` changes.
- [ ] **9e — Move Dreamer to `runtime/dreamer/`** — `dreamer.py` (top level), 
      `runtime/dreamer_candidates.py`, and `runtime/dreamer_eval.py` all move to
      `runtime/dreamer/analysis.py`, `candidates.py`, `eval.py`. Move
      `runtime/longitudinal_benchmark.py` to `eval/`.
- [ ] **9f — Reorganize `runtime/` into subdirectories** — create `turn/`, `flush/`,
      `session/`, `passes/`, `queue/`, `observability/` subdirectories. Each `__init__.py`
      re-exports old module symbols as a migration shim.
- [ ] **9g — Thin `integrations/api.py`** — audit all 22+ outgoing imports; any that
      reach internal modules (`runtime.*`, `claim.*`, etc.) are replaced with imports
      from `core_memory`'s public `__all__`. Gaps in the public API are filled in
      `__init__.py` first.

**Risk:** Medium per sub-task, high in aggregate. Do one sub-task per PR. Never batch.

---

## Phase 10 — Documentation Consolidation

**PRD:** `docs/PRD/10-documentation-consolidation.md`

- [ ] **10a — Archive 11 stray `v2_p*` files** from `docs/` root to
      `docs/archive/history/` (they belong there with the rest of the phase history)
- [ ] **10b — Retire `docs/ARCHITECTURE.md`** — it references pre-v2 file names; merge
      any still-accurate content into `architecture_overview.md`, then archive it
- [ ] **10c — Update `architecture_overview.md`** to reflect post-Phase-9 directory
      structure (new runtime subdirs, cli/ package, integrations/openclaw/, capability
      tiers, init wizard). Target: 100–150 lines, readable in 5 minutes.
- [ ] **10d — Audit and classify all docs/ root files** (30+ files) as Current, Snapshot,
      or Superseded. Move Snapshot/Superseded files to `docs/reports/` or `docs/archive/`.
      Every Current file gets verified and listed in `docs/index.md`.
- [ ] **10e — Create `docs/status.md`** — single tracked-state document merging
      `demo/TODO.md`'s correctness items (#1–#7), the cleanup workstream (phases 0–9),
      and current completion states. `demo/TODO.md` becomes a pointer to it.
- [ ] **10f — Add `docs/PRD/README.md`** — index of all PRD files with one-line
      descriptions and statuses.
- [ ] **10g — Update `docs/index.md`** — fix broken links from Phase 9 file moves,
      add PRD section, add open-workstreams section, fix Neo4j label, verify all eval/
      links resolve after longitudinal_benchmark.py moved.

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
