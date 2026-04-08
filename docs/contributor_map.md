# Contributor Map (10-minute orientation)

If you're new, this is the shortest map of where behavior lives.

## Canonical write authority
- `core_memory/runtime/engine.py`
  - public write boundaries only:
    - `process_turn_finalized(...)`
    - `process_session_start(...)`
    - `process_flush(...)`
- lifecycle implementation modules under `core_memory/runtime/`:
  - `turn_flow.py`, `turn_prep.py`, `session_start_flow.py`, `flush_flow.py`, `turn_quality.py`, `flush_state.py`

## Canonical retrieval
- tool entrypoints: `core_memory/retrieval/tools/memory.py`
- request-first pipeline: `core_memory/retrieval/pipeline/`
- canonical traversal surface: `core_memory/graph/traversal.py`

## Compatibility / lower-level persistence
- `core_memory/persistence/store.py` (facade + persistence orchestration)
- core add-bead write path helper: `core_memory/persistence/store_add_bead_ops.py`
- bootstrap + dream helper ops: `core_memory/persistence/store_dream_bootstrap_ops.py`
- failure-signature compatibility helpers: `core_memory/persistence/store_failure_ops.py`
- initialization/config/bootstrap helper: `core_memory/persistence/store_init_ops.py`
- json read/write + enum normalization helpers: `core_memory/persistence/store_json_ops.py`
- tokenization/query-intent/hygiene wrappers: `core_memory/persistence/store_text_hygiene_ops.py`
- add-bead helper heuristics: `core_memory/persistence/store_add_helpers.py`
- bead validation helpers: `core_memory/persistence/store_validation_helpers.py`
- constraint retrieval/compliance helpers: `core_memory/persistence/store_constraints.py`
- query/read helper surface: `core_memory/persistence/store_query.py`
- context-aware retrieval helper: `core_memory/persistence/store_retrieval_context.py`
- session turn/consolidation helpers: `core_memory/persistence/store_session_ops.py`
- promote/link/recall/stats helpers: `core_memory/persistence/store_relationship_ops.py`
- compaction/archive/myelination helpers: `core_memory/persistence/store_compaction_ops.py`
- index projection rebuild helper: `core_memory/persistence/store_projection_ops.py`
- autonomy KPI/reinforcement helpers: `core_memory/persistence/store_autonomy_ops.py`
- heads/index update helpers: `core_memory/persistence/store_index_heads_ops.py`
- promotion policy service: `core_memory/persistence/promotion_service.py`
- reporting service: `core_memory/reporting/store_reporting.py`
- metrics runtime service: `core_memory/reporting/store_metrics_runtime.py`
- rationale scoring service: `core_memory/reporting/store_rationale.py`

## CLI surfaces
- thin entrypoint + routing: `core_memory/cli.py`
- compatibility alias logic: `core_memory/cli_compat.py`
- memory parser surface: `core_memory/cli_parser_memory.py`
- memory command handlers: `core_memory/cli_memory_handlers.py`
- graph handlers: `core_memory/cli_handlers_graph.py`
- metrics handlers: `core_memory/cli_handlers_metrics.py`
- store/maintenance handlers: `core_memory/cli_handlers_store.py`

## Async/side effects (current and target)
- async substrate entrypoints: `core_memory/runtime/jobs.py`
- compaction queue primitive: `core_memory/runtime/compaction_queue.py`
- side-effect queue primitive: `core_memory/runtime/side_effect_queue.py`
- post-write side-effect enqueue policy: `core_memory/runtime/side_effects.py`

## Dreamer
- implementation: `core_memory/dreamer.py`
- candidate queue + adjudication helpers: `core_memory/runtime/dreamer_candidates.py`
- contract doc: `docs/dreamer_contract.md`
- role: async candidate-generating subsystem with reviewable adjudication path.

## Product tiers
- **Required**: canonical runtime write boundaries + canonical retrieval tools
- **Recommended**: semantic extras + strict semantic mode where needed
- **Compatibility**: `MemoryStore`, helper ingress APIs, legacy CLI aliases
- **Experimental**: optional adapters/evals not listed as canonical

## Test naming convention (behavior-first)
- Prefer behavior intent in test filenames (for example: `test_turn_event_to_retrieval_contract.py`)
- Avoid internal phase/ticket labels in new filenames (`p8b`, `v2p19`, `sliceNN`) when behavior wording is practical
- Keep tests discoverable with `test_*.py` and group by product behavior rather than implementation milestone
