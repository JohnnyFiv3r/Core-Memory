# Core Memory Streamline Plan (Architectural)

Date: 2026-03-11  
Branch: `arch/streamline-file-tree-plan`

## Intent
Move the repository from an evolutionary layout to an explicit, canonical architecture where:
- event-driven write runtime is unmistakably primary,
- retrieval/tooling surfaces are stable and easy to consume,
- legacy compatibility paths are isolated and eventually removable,
- `store.py` is reduced to persistence-only ownership.

This plan is optimized for readability, low blast radius, and incremental shipping.

---

## Design Principles
1. **Authority clarity beats convenience**: event/session surfaces are authority; projections are caches.
2. **One module, one reason to change**: orchestration, policy, retrieval, persistence are separated.
3. **Public surface stability**: external integrations keep stable import paths while internals evolve.
4. **Deprecation by quarantine**: legacy kept in explicit `legacy/` boundaries with timed removal.
5. **Small mergeable PRs**: every step must be reversible and test-gated.

---

## Target File Tree (Optimal Streamlined Shape)

```text
core_memory/
  __init__.py

  runtime/                         # canonical write-side runtime
    ingress.py                     # (from event_ingress.py)
    state.py                       # (from event_state.py)
    engine.py                      # (from memory_engine.py)
    worker.py                      # (from event_worker.py)
    session_surface.py             # (from session_surface.py)
    live_session.py                # (from live_session.py)

  persistence/                     # durable storage and rebuildable projections
    store.py                       # slim MemoryStore façade only
    events.py                      # append-only event log utilities
    io.py                          # lock/atomic/jsonl primitives
    archive_index.py               # archive snapshot index
    rolling_record_store.py        # continuity authority record store

  retrieval/                       # read-side normalization, ranking, gating
    query_norm.py
    lexical.py
    hybrid.py
    rerank.py
    quality_gate.py
    context_recall.py
    failure_patterns.py
    search_form.py
    types.py
    config.py

  memory_api/                      # user/tool-facing memory operations
    tools.py                       # replaces tools/memory.py internals
    reason.py                      # from tools/memory_reason.py
    search.py                      # from tools/memory_search.py
    execute.py                     # from memory_skill/execute.py
    catalog.py                     # from memory_skill/catalog.py
    snap.py                        # from memory_skill/snap.py
    explain.py                     # from memory_skill/explain.py

  graph/
    structural.py                  # from graph_structural.py
    semantic.py                    # from graph_semantic.py
    traversal.py                   # from graph_traversal.py
    api.py                         # from graph.py façade

  policy/                          # promotion/constraint/hygiene decision logic
    promotion.py
    hygiene.py                     # from hygiene.py
    incidents.py                   # from incidents.py

  integrations/
    api.py                         # stable ingress API
    openclaw_agent_end_bridge.py
    http/
      server.py
    pydanticai/
      run.py
    springai/
      bridge.py

  schema/
    models.py                      # from models.py
    normalization.py               # from schema.py aliases/normalizers

  legacy/                          # explicit compatibility quarantine
    openclaw_integration.py        # deprecated shim
    trigger_orchestrator.py        # deprecated shim
    write_triggers.py              # deprecated shim
    rolling_surface.py             # deprecated renderer
    association_pass_engine.py     # deprecated helper

  cli/
    main.py                        # from cli.py
    commands/
      core.py
      retrieval.py
      graph.py
      metrics.py
      integration.py
      maintenance.py

  data/
    incidents.json
    structural_relation_map.json
    topic_aliases.json
```

Notes:
- Keep temporary compatibility re-exports at old import paths for one migration window.
- `legacy/` must be discoverable in docs and blocked from new usage via lint/tests.

---

## What Moves First (Execution Order)

### Phase 1 — Architecture labels and import-safe scaffolding
- Create package folders (`runtime`, `persistence`, `memory_api`, `legacy`, etc.).
- Add thin wrappers/re-exports so no external imports break.
- Add `docs/canonical_paths.md` update to mirror new structure.

**Exit criteria**: tests pass unchanged; no behavior changes.

### Phase 2 — `store.py` decomposition (highest value)
Extract from `store.py` into focused modules:
- `policy` logic (promotion/validation/hygiene helpers),
- metrics run state/reporting,
- migration/legacy import helpers.

Keep `MemoryStore` as orchestration façade over persistence primitives.

**Exit criteria**: store file reduced materially; tests unchanged.

### Phase 3 — Runtime consolidation under `runtime/`
- Move ingress/state/engine/worker/session-read surfaces under `runtime/`.
- Maintain compatibility imports at previous paths with deprecation warnings.

**Exit criteria**: canonical docs and code paths align 1:1.

### Phase 4 — Memory API unification
- Consolidate `tools/` + `memory_skill/` under `memory_api/` while preserving public wrappers.
- Ensure execute/search/reason contracts remain stable.

**Exit criteria**: integration tests + contract tests green.

### Phase 5 — Legacy quarantine and enforcement
- Move deprecated files into `legacy/` namespace.
- Add tests/lints to prevent new imports from `legacy/` outside explicitly allowed shims.

**Exit criteria**: no new dependency on legacy modules.

### Phase 6 — CLI modularization
- Split monolithic `cli.py` into command modules.
- Keep command UX exactly stable.

**Exit criteria**: CLI parity tests green.

---

## Guardrails (Non-Negotiable)
1. **Idempotency contract unchanged** (`session_id:turn_id` pass claim semantics).
2. **Event/session write authority unchanged**.
3. **Projection rebuildability preserved** (`events.rebuild_index`).
4. **Public integration signatures stable** (`integrations/api.py`, tool wrappers).
5. **Deterministic ordering and tie-break rules unchanged** on retrieval outputs.

---

## Deletion Policy (When to Actually Remove)
A legacy file is removable only when all are true:
- No production import references,
- No tests depend on it directly,
- Replacement path has been stable for at least one release cycle,
- Migration note is documented in changelog.

---

## Recommended PR Stack
1. `PR-01`: package scaffolding + import-compatible wrappers.
2. `PR-02`: store extraction — policy/hygiene split.
3. `PR-03`: store extraction — metrics/migration split.
4. `PR-04`: runtime namespace migration.
5. `PR-05`: memory_api unification.
6. `PR-06`: legacy quarantine + import guard tests.
7. `PR-07`: CLI command modularization.
8. `PR-08`: dead code deletions (post-stability window).

Each PR should include:
- updated architecture map,
- explicit contract checklist,
- before/after module ownership table.

---

## Final Outcome
After this plan, the repo reads like a deliberate architecture:
- `runtime` = write-side truth,
- `persistence` = durability and projection mechanics,
- `retrieval` + `memory_api` = deterministic read/reason surfaces,
- `legacy` = explicitly shrinking compatibility island,
- `store` = slim and auditable.

That is the shape most likely to hold up under growth, contributors, and integrations without reintroducing path ambiguity.
