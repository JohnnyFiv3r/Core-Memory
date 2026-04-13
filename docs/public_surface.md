# Public Surface

Status: Canonical

This page defines what external integrators should call.

## Plain-English contract (quick read)

- **Required:** `process_turn_finalized(...)` for writes and `memory.search/trace/execute` for reads.
- **Recommended:** install semantic extras for strict semantic retrieval behavior.
- **Compatibility:** `MemoryStore` and helper ingress APIs remain supported for migration/advanced workflows.
- **Experimental:** optional adapter/eval surfaces that are not listed under canonical runtime surfaces.

Term translations:
- "canonical semantic mode" = strict semantic retrieval mode
- `degraded_allowed` = allow lexical fallback if semantic backend is missing
- "hydration" = load source details after selecting retrieval results

## Canonical decision rule
A surface is canonical only if it is both:
1. tested as active contract, and
2. documented as forward-supported in current docs.

## Write ingress
- `core_memory.runtime.engine.process_turn_finalized(...)` — canonical per-turn write boundary
- `core_memory.runtime.engine.process_session_start(...)` — canonical session-start lifecycle boundary
- `core_memory.runtime.engine.process_flush(...)` — canonical session-end flush boundary
- `core_memory.integrations.api.emit_turn_finalized(...)` — ingress helper used by adapters that defer in-process turn handling

## Retrieval/runtime tool surface
- `core_memory.retrieval.tools.memory.search(request: dict, root='.', explain=False)` — canonical anchor retrieval.
- `core_memory.retrieval.tools.memory.trace(query='', anchor_ids=[...], root='.', k=..., hydration=...)` — canonical causal traversal after anchor identification.
- `core_memory.retrieval.tools.memory.execute(request: dict, root='.', explain=False)` — unified memory request entrypoint.

## Inspect/observability read family (canonical)

Use these public inspect surfaces for dashboards/demos/adapter observability
instead of direct `.beads` / `.turns` layout reads:

- `core_memory.integrations.api.inspect_state(...)`
- `core_memory.integrations.api.inspect_bead(...)`
- `core_memory.integrations.api.inspect_bead_hydration(...)`
- `core_memory.integrations.api.inspect_claim_slot(...)`
- `core_memory.integrations.api.list_turn_summaries(...)`

Convenience package-root aliases are also exported:
- `core_memory.memory_search`
- `core_memory.memory_trace`
- `core_memory.memory_execute`

## Async job/queue operations (canonical ops surface)
- `core_memory.runtime.jobs.async_jobs_status(root='...')`
- `core_memory.runtime.jobs.enqueue_async_job(root='...', kind='semantic-rebuild|compaction|dreamer-run|neo4j-sync|health-recompute', ...)`
- `core_memory.runtime.jobs.run_async_jobs(root='...', run_semantic=True, max_compaction=1, max_side_effects=2)`

Dreamer candidate queue surfaces:
- `core_memory.runtime.dreamer_candidates.enqueue_dreamer_candidates(...)`
- `core_memory.runtime.dreamer_candidates.list_dreamer_candidates(...)`
- `core_memory.runtime.dreamer_candidates.decide_dreamer_candidate(...)`

All async ops payloads include:
- `schema_version = "core_memory.async_jobs.v1"`

CLI operators map to these runtime ops:
- `core-memory ops jobs-status`
- `core-memory ops jobs-enqueue --kind semantic-rebuild|compaction|dreamer-run|neo4j-sync|health-recompute`
- `core-memory ops jobs-run [--max-compaction N] [--max-side-effects N] [--no-semantic]`
- `core-memory ops dreamer-candidates [--status pending|accepted|rejected] [--limit N]`
- `core-memory ops dreamer-decide --id <candidate-id> --decision accept|reject [--apply]`

### Retrieval semantics
- `search`: anchor retrieval
- `trace`: causal traversal/grounding after anchor identification
- `execute`: unified orchestration entrypoint

Compatibility note:
- `form_submission` is accepted as an alias for `request` in compatibility callers, but forward docs and adapter contracts should use `request`.

### Semantic mode contract
- Query-based anchor lookup uses semantic backend by default.
- If semantic backend is unavailable and `CORE_MEMORY_CANONICAL_SEMANTIC_MODE=required`, payloads return:
  - `ok=false`
  - `error.code="semantic_backend_unavailable"`
  - `degraded=false`
- In `degraded_allowed` mode, payloads remain `ok=true` with explicit `degraded=true` + degradation warnings.

Semantic backend deployment guidance:
- `faiss-*` local backend is intended for dev/single-process use.
- `qdrant` and `pgvector` are recommended for distributed-safe production deployments.
- For backend mode details, see `docs/semantic_backend_modes.md`.

Trace calls with explicit `anchor_ids` bypass semantic anchor lookup.

### Hydration semantics
Hydration is explicit post-selection source recovery (turn/tool/adjacent payloads).
It is not a replacement for retrieval planning.

Deep recall is separate from canonical hydration.

### Session-start vs continuity semantics
- `session_start` is a first-class lifecycle write boundary and continuity snapshot bead.
- `core_memory.write_pipeline.continuity_injection.load_continuity_injection(...)` is a pure read helper over continuity authority surfaces.
- Continuity reads must not implicitly create beads or mark semantic state dirty.
- Adapters that support session start must invoke explicit adapter-owned boundary logic.

## Adapter entrypoints
- OpenClaw bridge surfaces under `core_memory.integrations.openclaw.*`
- PydanticAI surfaces under `core_memory.integrations.pydanticai.*`
- SpringAI/HTTP surfaces under `core_memory.integrations.http.*` and docs contract
- MCP typed read surfaces under `core_memory.integrations.mcp.typed_read.*`
- MCP typed write surfaces under `core_memory.integrations.mcp.typed_write.*`
- LangChain surfaces under `core_memory.integrations.langchain.*`

HTTP async ops surfaces (operator tooling):
- `GET /v1/ops/async-jobs/status`
- `POST /v1/ops/async-jobs/enqueue`
- `POST /v1/ops/async-jobs/run`
- `GET /v1/ops/dreamer/candidates`
- `POST /v1/ops/dreamer/candidates/decide`

HTTP inspect read surfaces:
- `GET /v1/memory/inspect/state`
- `GET /v1/memory/inspect/beads/{bead_id}`
- `GET /v1/memory/inspect/beads/{bead_id}/hydrate`
- `GET /v1/memory/inspect/claim-slots/{subject}/{slot}`
- `GET /v1/memory/inspect/turns`

HTTP MCP typed read surfaces:
- `POST /v1/mcp/query-current-state`
- `POST /v1/mcp/query-temporal-window`
- `POST /v1/mcp/query-causal-chain`
- `POST /v1/mcp/query-contradictions`

HTTP MCP typed write surfaces:
- `POST /v1/mcp/write-turn-finalized`
- `POST /v1/mcp/apply-reviewed-proposal`
- `POST /v1/mcp/submit-entity-merge-proposal`

## Compatibility / non-primary
- Archived historical docs and migration artifacts under `docs/archive/` and `docs/reports/`
- Non-canonical helper modules may remain in-tree but are not forward contract unless listed above
