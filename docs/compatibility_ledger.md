# Compatibility Ledger

**Status:** Canonical cleanup-governance reference and post-cleanup deprecation backlog

This ledger names compatibility surfaces that remain in the tree on purpose. It
does not make every historical path a forward API. It tells reviewers which
paths are public, which are private/internal, what replaces them, and what proof
is required before removal.

The architecture cleanup workstream is closed at the architecture layer. The
architecture guard baseline is zero; this ledger now owns the remaining
compatibility-governance work. Public compatibility surfaces below are backlog
items for future deprecation windows, not files to delete during cleanup.

Compatibility removals must satisfy the row's removal condition and keep the
architecture guard baseline honest. When a row is repaired, shrink
`scripts/architecture_guards_baseline.json` in the same PR.

The active compatibility-surface ratchet lives in
`scripts/compat_surface_usage_baseline.json`. As of the 2026-07-08 cleanup
closeout pass, the governed first-party counts are:

- `graph/api.py`: 0 active first-party imports outside the facade module;
  retained for external legacy imports.
- `core_memory/persistence/encryption.py`: 1 compatibility test import.
- `core_memory.runtime.semantic_tasks`: 4 compatibility test references.
- `form_submission`: 13 accepted alias references, limited to normalizers,
  contract/docs, and focused compatibility tests.
- `MemoryStore.dream(...)`: 2 bridge compatibility test calls.

## Classification Rules

- **Public:** external users, adapters, scripts, or persisted data may depend on
  the surface. Removal requires a deprecation note or breaking-change window.
- **Private/internal:** first-party implementation detail. External callers
  should not use it, but current repo behavior or tests may still depend on it.
- **Artifact debt:** retained file or facade from an older shape. It should not
  gain new callers.

## Post-Cleanup Deprecation Backlog

These surfaces remain after architecture cleanup because external callers,
persisted data, or documented adapters may depend on them. Future removal must
follow each row's deprecation or breaking-change condition and keep both guard
baselines green.

- Public import/API facades: `core_memory/graph/api.py`,
  `core_memory/retrieval/tools/memory_search.py`,
  `core_memory/persistence/encryption.py`, and `MemoryStore.dream(...)`.
- Public compatibility request/data shapes: `form_submission` and legacy event
  schema constants/imports.
- Public compatibility module paths: `core_memory.runtime.semantic_tasks.*`.
- Public CLI compatibility: top-level legacy commands, supported internally by
  `core_memory/cli/compat.py`.

## Ledger

| Surface | Classification | Current role | Replacement path | Deprecation/removal condition | Proving test or gate |
|---|---|---|---|---|---|
| `core_memory/graph/api.py` | Public compatibility facade | Preserves legacy `core_memory.graph.api.*` imports while graph logic lives in split modules. Current first-party import scan has 0 active callers outside this facade module; the row exists for external legacy callers. | `core_memory.graph.core`, `core_memory.graph.structural`, `core_memory.graph.traversal`, `core_memory.graph.semantic`, and package re-exports from `core_memory.graph` | Future removal requires a breaking-change/deprecation window plus `rg "core_memory.graph.api" core_memory tests docs` showing no active callers except historical docs/ledger. Do not delete as a cleanup shortcut. | `pytest -m facade`, `tests/test_association_confidence_compat.py`, `scripts/check_architecture_guards.py --fail-on-new`, active import scan |
| Top-level legacy CLI commands (`add`, `query`, `stats`, `doctor`, `heads`, `preflight`, `constraints`, `check-plan`, `retrieve-context`, `dream`, `rebuild`, `compact`, `consolidate`, `rolling-window`, `uncompact`, `myelinate`, `sidecar`, `openclaw`, `graph`, `metrics`, `async-jobs-*`, `dreamer-*`) | Public CLI compatibility | Keeps existing automation working while grouped command families become the forward surface | `setup`, `store`, `memory`, `recall`, `inspect`, `ingest`, `integrations`, `ops`, `semantic`, `mcp`, `myelination`, `dev` | Remove only in a major version or documented deprecation pass after docs, scripts, plugin config, and tests no longer call the alias. | `tests/test_cli_grouped_surface.py`, `tests/test_cli_taxonomy.py`, `tests/test_runtime_jobs_cli.py`, `tests/test_cli_compat_module.py` |
| `core-memory graph backfill-causal-links --apply` | Public CLI compatibility | Applies lexical-overlap-derived `supports`/`derived_from` links and structural edges directly. This deterministic semantic mutation conflicts with the agent-led write invariant but may be used by existing operator automation. | Relationship-neutral candidate generation followed by the governed association agent judge/apply flow (`maintain` association actions) | The agent-led semantic-write rollout first ships the replacement and emits a deprecation warning plus telemetry while preserving `--apply` for one published deprecation release. In the following documented release, `--apply` is rejected with migration guidance. Removal/cutover must include release notes and focused CLI compatibility tests. | `tests/test_graph_r2.py`, association coverage/judge tests, CLI parser/handler tests, `scripts/check_architecture_guards.py --fail-on-new-compat` |
| `core_memory/cli/compat.py` | Private/internal compatibility adapter | Rewrites legacy argv shapes and maps grouped aliases to canonical handlers | Direct parser/handler dispatch in `core_memory/cli/__init__.py` and `core_memory/cli/handlers/*` | Remove only after the top-level CLI alias row is retired and no tests import `core_memory.cli.compat`. | `tests/test_cli_compat_module.py`, `tests/test_cli_grouped_surface.py` |
| Runtime event schema import path `core_memory/runtime/event_schemas.py` and legacy constants (`*_LEGACY`) | Public persisted-data read/import compatibility | Preserves the historical runtime import path and lets readers accept historical `openclaw.memory.*` event schema strings without emitting them from new code. `core-memory ops event-schema-audit` now provides a read-only store audit for supported legacy schema rows. | Canonical constants and helpers in `core_memory.schema.event_schemas` | Remove the runtime import path only after a breaking-change/deprecation window. Remove legacy aliases only after `core-memory ops event-schema-audit` or an equivalent migration/rebuild proof shows no supported store contains legacy event schema rows, or after a breaking-change window explicitly drops legacy event reads. | `tests/test_flush_report_artifact.py`, `tests/test_event_import_migration_guard.py`, `tests/test_event_schema_audit.py` |
| Runtime semantic task contracts import path `core_memory/runtime/semantic_tasks/contracts.py` | Public contract import compatibility | Preserves the historical runtime import path for semantic task dataclasses, constants, task names, and model-tier constants. Current first-party usage is 1 compatibility test reference. | `core_memory.schema.semantic_tasks` | Future removal requires a breaking-change/deprecation window and an import scan showing no active external-facing docs, examples, or tests rely on the runtime contracts path. | `tests/test_semantic_task_contracts.py`, semantic-task focused tests, `scripts/check_architecture_guards.py --fail-on-new` |
| Runtime semantic task runtime, verifier, and receipt import paths (`core_memory/runtime/semantic_tasks/runtime.py`, `verifier.py`, `receipts.py`) | Public compatibility facades | Preserve historical imports for the semantic task provider runtime, output verifier, and run-receipt helpers after implementation ownership moved to policy/persistence. Current first-party usage is 3 compatibility test references. | `core_memory.policy.semantic_task_runtime`, `core_memory.policy.semantic_task_verifier`, `core_memory.persistence.semantic_task_receipts` | Future removal requires a breaking-change/deprecation window plus proof that active external docs/examples/tests no longer rely on runtime module-level imports. Do not reintroduce lower-layer imports to the runtime facade. | `tests/test_semantic_task_boundary_compat.py`, `tests/test_semantic_task_runtime.py`, `tests/test_semantic_task_verifier.py`, `scripts/check_architecture_guards.py --fail-on-new` |
| Retrieval request alias `form_submission` | Public request compatibility | Accepts older typed-search payloads in Python, HTTP, and adapter bridge callers as a deprecated compatibility alias; not recommended for new callers. Current compat baseline is 13 accepted references across normalizers, contracts/docs, and focused compatibility tests. | `request={...}` for `core_memory.retrieval.tools.memory.search`, `/v1/memory/search`, and adapter bridges; `recall(...)` as the primary read surface | Future removal requires OpenClaw/PydanticAI/HTTP docs and tests to use `request` only, a deprecation note for external callers, and a breaking-change/deprecation window. | `tests/test_memory_search_request_canonical.py`, `tests/test_memory_search_internal_isolation.py`, `tests/test_openclaw_read_bridge.py`, `tests/test_http_ingress.py`, `scripts/check_architecture_guards.py --fail-on-new-compat` |
| `core_memory/retrieval/tools/memory_search.py` | Public compatibility wrapper | Preserves the older typed `search_typed(...)` import path and result envelope; first-party usage is reduced to the dedicated wrapper contract test | `core_memory.retrieval.tools.memory.search(request=...)` or root alias `core_memory.memory_search(...)` | Remove only after adapter docs and validation no longer recommend `memory_search.py`, package-root read surfaces remain covered, and a breaking-change/deprecation window has passed. | `tests/test_memory_search_tool_wrapper.py`, `tests/test_package_root_public_surface.py`, `tests/test_pydanticai_memory_tools.py` |
| `core_memory/persistence/encryption.py` | Public optional compatibility module | Optional Fernet helpers for callers that imported encryption support directly. It is not part of the default write path; current first-party usage is 1 compatibility test import. | Future explicit encrypted backend or documented storage encryption extension point | Do not delete as a dead-file cleanup. Future removal requires a breaking-change process, a replacement encryption story, and an active import scan before any removal. | `tests/test_persistence_encryption_compat.py` plus an active import scan before any removal |
| Persistence helper modules (`store_add_helpers.py`, `store_*_ops.py`, `promotion_service.py`) | Private/internal implementation | Store implementation and policy helpers, some still crossing architectural boundaries | Future post-write effects boundary owned by runtime; storage modules keep durable data operations | Do not delete wholesale. Boundary cleanup should move one side-effect cluster at a time and shrink guard allowlists after each repair. | Focused touched-module tests plus `python scripts/check_architecture_guards.py --fail-on-new` |
| `MemoryStore.dream(...)` | Public legacy convenience bridge | Lets older store-oriented callers invoke Dreamer association analysis without importing Dreamer directly; active first-party references are limited to 2 bridge compatibility test calls. | Runtime Dreamer surfaces such as `core_memory.runtime.dreamer.analysis.run_analysis(...)` and queued side effects | Future removal requires store-oriented Dreamer usage to be deprecated after CLI/runtime migration has held through a breaking-change window. The store method must not reintroduce static persistence-to-runtime imports. | `tests/test_cli_handler_modules.py`, `tests/test_store_dream_bootstrap_ops_delegation.py`, `tests/test_dreamer_analysis.py`, `scripts/check_architecture_guards.py --fail-on-new-compat` |
## Recently Retired Artifacts

- `core_memory/persistence/store_core_delegates_mixin.py` and
  `core_memory/persistence/store_reporting_promotion_mixin.py` were retired after
  method inlining into `MemoryStore`. The proving gate was an active import scan
  for `StoreCoreDelegatesMixin|StoreReportingPromotionMixin`,
  `pytest -m mixin_assembly`, and an architecture-guard baseline shrink.
- `core_memory/retrieval/pipeline/explain.py` was retired after the live explain
  payload had already moved inline to `core_memory.retrieval.pipeline` and an
  active import scan for `retrieval.pipeline.explain|build_explain` returned no
  callers outside docs/ledger/release notes.
- `core_memory/persistence/write_ops.py` was retired after write behavior had
  already settled on `MemoryStore` methods and canonical runtime boundaries. The
  proving gate was an active import scan for `persistence.write_ops|write_ops`
  and focused package-root/write-path tests.
- `core_memory/retrieval/trace.py` was retired after active imports migrated to
  `core_memory.retrieval.pipeline.canonical.trace_request` or the public
  low-level tool surface `core_memory.retrieval.tools.memory.trace`. The proving
  gate was an import scan for `core_memory.retrieval.trace` and trace-depth/tool
  tests.
- `core_memory/management.py` moved to `core_memory/management/__init__.py` to
  clear root flat-file debt while preserving the public `core_memory.management`
  import path and package-root exports.
- `core_memory/cli_handlers_semantic.py` was retired after the live semantic CLI
  handler had already moved to `core_memory/cli/handlers/semantic.py`. The
  proving gate was an active import scan for
  `core_memory.cli_handlers_semantic|cli_handlers_semantic` and semantic CLI
  focused tests.
- `core_memory/runtime/goal_lifecycle.py` was retired after the live goal
  lifecycle pass had already moved to
  `core_memory/runtime/session/goal_lifecycle.py`. The proving gate was an
  active import scan for `core_memory.runtime.goal_lifecycle|runtime.goal_lifecycle`
  and goal lifecycle focused tests.
- `core_memory/runtime/session_enrichment_delta.py` was retired after the live
  session enrichment delta normalizer had already moved to
  `core_memory/runtime/session/session_enrichment_delta.py`. The proving gate was
  an active import scan for
  `core_memory.runtime.session_enrichment_delta|runtime.session_enrichment_delta`
  and session enrichment focused tests.
- `core_memory/runtime/source_envelope.py` moved to
  `core_memory/runtime/ingest/source_envelope.py` after active callers were
  migrated directly to the ingest-owned module. No root compatibility shim was
  retained. The proving gate was an active import scan for
  `core_memory.runtime.source_envelope|runtime.source_envelope` and source
  attribution focused tests.
- `core_memory/retrieval/failure_patterns.py` moved to
  `core_memory/persistence/failure_patterns.py` after active caller review
  showed the failure-signature helpers are store preflight logic, not retrieval
  behavior. No compatibility shim was retained. The proving gate was an active
  import scan for `core_memory.retrieval.failure_patterns|retrieval/failure_patterns`
  plus store failure focused tests.
- `core_memory/runtime/turn/turn_archive.py` moved to
  `core_memory/persistence/turn_archive.py` after active caller review showed
  the `.turns/` archive is durable local storage IO, not runtime orchestration.
  No compatibility shim was retained. The proving gate was an active import
  scan for `core_memory.runtime.turn.turn_archive|runtime/turn/turn_archive`
  plus turn archive and recording/source attribution focused tests.
- `core_memory/persistence/store_text_hygiene_ops.py` now owns the store's
  deterministic tokenization, memory-intent detection, query-token expansion,
  redaction, bead-content sanitization, and constraint extraction behavior
  directly. This removed persistence-to-policy and persistence-to-retrieval
  imports without changing the `MemoryStore` helper surface.
- Source hydration implementation now lives in
  `core_memory/persistence/source_hydration.py`. The public
  `core_memory.integrations.api.hydrate_bead_sources(...)` wrapper is unchanged,
  while retrieval uses the persistence helper directly for best-effort
  post-selection hydration instead of importing the integrations API.
- Retrieval feedback JSONL implementation now lives in
  `core_memory/persistence/retrieval_feedback.py`. The existing
  `core_memory.runtime.observability.retrieval_feedback` import path remains as
  a compatibility surface for observability callers, while retrieval write paths
  depend downward on persistence.
- Myelination reward-event JSONL implementation now lives in
  `core_memory/persistence/myelination_rewards.py`. The existing
  `core_memory.runtime.observability.myelination_rewards` import path remains as
  a compatibility surface for observability callers, while persistence approval,
  confirmation, and goal-resolution paths depend downward on persistence.
- Myelination calibration reads now live in
  `core_memory/persistence/calibration.py`. The existing
  `core_memory.runtime.observability.calibration` import path remains as a
  compatibility surface for public observability callers, while SOUL reads the
  lower persistence helper directly for auto-mode gating.
- SOUL's Dreamer bridge now reads pending candidates through
  `core_memory/persistence/dreamer_candidate_store.py` instead of the public
  Dreamer runtime command surface. Public Dreamer candidate enqueue/list/decide
  APIs remain in `core_memory.runtime.dreamer.candidates`.
- Identity/value read-side signal detection now lives in
  `core_memory/soul/identity_value_signals.py`. The existing
  `core_memory.runtime.dreamer.identity_value_research` import path keeps
  Dreamer candidate enqueueing and re-exports the detector for current callers.
- Goal-conflict tension signal detection now lives in
  `core_memory/soul/tension_signals.py`. The existing
  `core_memory.runtime.dreamer.tension_discovery` import path keeps Dreamer
  tension-candidate enqueueing and preserves assembly-depth annotations for
  current callers.
- Assembly Depth scoring now lives in `core_memory/soul/assembly_depth.py`.
  The existing `core_memory.runtime.dreamer.assembly_depth` import path remains
  the Dreamer compatibility wrapper and supplies live runtime myelination
  bonuses for current callers.
- Store metrics runtime state now lives in
  `core_memory/persistence/store_metrics_runtime.py`; the old reporting module
  was retired after active caller review showed it was only a `MemoryStore`
  persistence helper.
- Store rationale recall scoring now lives in
  `core_memory/persistence/store_rationale.py`; the old reporting module was
  retired after active caller review showed it was only a `MemoryStore`
  persistence helper.
- Store reporting aggregation now lives in
  `core_memory/persistence/store_reporting.py`; the old direct reporting module
  was retired after active caller review showed the forward-supported surface is
  `MemoryStore.metrics_report(...)`, `MemoryStore.autonomy_report(...)`,
  `MemoryStore.schema_quality_report(...)`, and the existing package-level
  `core_memory.reporting` exports.
- Entity registry and merge-review index implementations now live in
  `core_memory/persistence/entity_registry.py` and
  `core_memory/persistence/entity_merge_flow.py`; the existing
  `core_memory.entity.registry` and `core_memory.entity.merge_flow` import
  paths remain domain-facing exports for current callers.
- Semantic lifecycle manifest, queue, checkpoint, and trace-dirty state now
  lives in `core_memory/persistence/semantic_lifecycle.py`. The existing
  `core_memory.retrieval.lifecycle` import path remains the public retrieval
  lifecycle/autodrain surface for CLI/runtime callers.
- Retrieval semantic autodrain no longer imports the runtime async-job runner
  statically. `core_memory.retrieval.lifecycle` still owns the public lifecycle
  facade and resolves `core_memory.runtime.queue.jobs.run_async_jobs(...)` at
  call time for background drains.
- Bead post-write side effects now live in
  `core_memory/runtime/post_write/bead_commit.py`. The store add-bead path keeps
  durable persistence local and calls the runtime post-write boundary for
  vector, graph, sync-target, and association-coverage side effects.
- Bead write hygiene contract helpers now live in
  `core_memory/persistence/bead_hygiene_contract.py` so the store write path can
  normalize retrieval eligibility and bead richness without importing policy.
  `core_memory.policy.hygiene` remains the curated maintenance surface and
  re-exports the helper names for current callers.
- Private `MemoryStore._quick_association_candidates(...)` was removed after an
  active caller scan found no live callers outside historical docs. Public
  association preview remains available through `core_memory.association`.
- Semantic task runtime ownership moved out of `runtime/`: provider runtime and
  verifier implementation now live under `core_memory.policy`, while semantic
  task run receipts live under `core_memory.persistence`. The historical
  `core_memory.runtime.semantic_tasks` module paths remain public compatibility
  facades and are covered by `tests/test_semantic_task_boundary_compat.py`.
- `MemoryStore.dream(...)` no longer imports `core_memory.runtime.dreamer`
  statically from the persistence layer. It remains a legacy convenience bridge
  and resolves the Dreamer analysis provider at call time. CLI Dreamer analysis
  now calls `core_memory.runtime.dreamer.analysis.run_analysis(...)` directly,
  so first-party usage of the store bridge is limited to compatibility tests.

## Explicit Non-Compatibility

The presence of a file in historical reports under `docs/reports/` or
`docs/archive/` does not make it a supported surface. Current support requires a
row in this ledger or an entry in `docs/public_surface.md`.
