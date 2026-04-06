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
- promotion policy service: `core_memory/persistence/promotion_service.py`
- reporting service: `core_memory/reporting/store_reporting.py`

## CLI surfaces
- thin entrypoint + routing: `core_memory/cli.py`
- compatibility alias logic: `core_memory/cli_compat.py`
- memory parser surface: `core_memory/cli_parser_memory.py`
- memory command handlers: `core_memory/cli_memory_handlers.py`
- graph handlers: `core_memory/cli_handlers_graph.py`
- metrics handlers: `core_memory/cli_handlers_metrics.py`
- store/maintenance handlers: `core_memory/cli_handlers_store.py`

## Async/side effects (current and target)
- current side effects are still mostly in-process runtime orchestration.
- async substrate work is tracked in production-readiness roadmap (jobs/queue abstraction).

## Dreamer
- implementation: `core_memory/dreamer.py`
- current role: non-authoritative suggestion/analysis path.
- target role: async candidate-generating subsystem with reviewable adjudication path.

## Product tiers
- **Required**: canonical runtime write boundaries + canonical retrieval tools
- **Recommended**: semantic extras + strict semantic mode where needed
- **Compatibility**: `MemoryStore`, helper ingress APIs, legacy CLI aliases
- **Experimental**: optional adapters/evals not listed as canonical
