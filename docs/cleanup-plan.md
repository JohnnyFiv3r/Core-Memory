# Core Memory Cleanup Plan

**Created:** 2026-05-24
**Workstream:** Code hygiene + storage adapter boundary

This document tracks the cleanup and architectural improvement workstream identified from
codebase analysis. Each phase is a discrete, mergeable unit with its own test gate.
Do not start a phase until the previous one has passed CI.

PRDs for Phases 4–8 live in `docs/PRD/` and carry codebase-specific implementation detail.

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

**Goal:** Remove the 4 files with zero references anywhere in the repo.

- [ ] `core_memory/persistence/encryption.py` — stub with env-var docs, never imported
- [ ] `core_memory/persistence/write_ops.py` — delegating stub, never imported
- [ ] `core_memory/retrieval/vector_backend.py` — no imports anywhere
- [ ] `core_memory/retrieval/pipeline/explain.py` — defines `build_explain()`, never called

Procedure: `grep -r "<basename>"` across the entire repo to confirm zero refs, delete,
run full pytest suite.

**Risk:** None if Phase 0 CI is in place.

---

## Phase 2 — Fix Mislabeled Circular-Import Workarounds

**Goal:** Remove dead defensive code; correct misleading docstrings.

- [ ] `core_memory/retrieval/__init__.py` — remove the `__getattr__` lazy `recall` import.
      No actual cycle exists; replace with a normal `from .agent import recall`.
      Verify full test suite passes (a real cycle will fail immediately).
- [ ] `core_memory/runtime/__init__.py` — rewrite the docstring: the empty `__init__` is
      a lazy-loading optimization (defers loading heavy engine/job modules), not a
      circular-import fix. No code change needed.

**Risk:** Near zero. The first change is self-verifying via CI.

---

## Phase 3A — Harden the PydanticAI Boundary

**Goal:** Lock in that `pydantic-ai` is optional; prevent it silently becoming required.

- [ ] Add `pytest.mark.skipif(not importlib.util.find_spec("pydantic_ai"), reason="...")`
      guards to `tests/test_pydanticai_adapter.py` and `tests/test_pydanticai_memory_tools.py`
      (both currently have hard imports that fail without the extra)
- [ ] Add CI matrix entry in `test.yml` that installs `core-memory` without `[pydanticai]`
      and runs the full suite — confirms skip guards work and core doesn't depend on pydantic-ai
- [ ] Add a tox/pytest fixture that asserts `pydantic_ai` does NOT appear in `sys.modules`
      after `import core_memory` without the extra installed

**Risk:** Low. Additive guards only.

---

## Phase 4 — Remove `graph/api.py` Compat Facade

**PRD:** `docs/PRD/04-graph-module-cleanup.md`

- [ ] Fix `causal_traverse` signature: make `anchor_ids` keyword-only in the split module
      so the facade's positional-to-keyword conversion is no longer needed
- [ ] Migrate `core_memory/cli_handlers_graph.py:7-16` to import from split modules
      (`structural`, `traversal`, `semantic`) instead of `graph.api`
- [ ] Migrate all 12 test files that import from `core_memory.graph.api` to use split
      modules directly
- [ ] Update `core_memory/graph/__init__.py`: replace star-import from `api` with explicit
      re-exports from split modules (preserves `from core_memory.graph import build_graph`
      for external consumers)
- [ ] Delete `core_memory/graph/api.py`
- [ ] Optionally rename `_api_impl.py` — the `_` prefix was meaningful when `api.py` was
      the public surface; consider `core.py` or folding into split modules

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

## Sequence dependency

```
Phase 0 (CI) → must precede everything
Phase 1, 2, 3A → independent, can run in any order after Phase 0
Phase 4 → after Phase 0
Phase 5 → after Phase 4 (cleaner store surface makes audit more meaningful)
Phase 6 → after Phase 5 (flat ops surface makes protocol extension cleaner)
Phase 7 → after Phase 6 (needs extended StorageBackend protocol)
Phase 8 → after Phase 6 (needs BackendCapabilities + create_backend config support)
```

---

## What this workstream does NOT change

- Public API: `Memory`, `MemorySession`, `recall()`, HTTP routes, MCP tools
- Bead schema, causal edge types, supersession logic, rolling window semantics
- On-disk storage format (JSONL session files remain the source of truth)
- Existing TODO items #1–#7 in `demo/TODO.md` (separate correctness workstream)
