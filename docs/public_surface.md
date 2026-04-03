# Public Surface (Pre-OSS)

Status: Canonical

This page defines what external integrators should call.

## Canonical decision rule
A surface is canonical only if it is both:
1. tested as active contract, and
2. documented as forward-supported in current docs.

## Write ingress
- `core_memory.integrations.api.emit_turn_finalized(...)`

## Retrieval/runtime tool surface
- `core_memory.tools.memory.search(request: dict, root='.', explain=False)` — canonical anchor retrieval.
- `core_memory.tools.memory.trace(query='', anchor_ids=[...], root='.', k=..., hydration=...)` — canonical causal traversal after anchor identification.
- `core_memory.tools.memory.execute(request: dict, root='.', explain=False)` — unified memory request entrypoint.

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

Trace calls with explicit `anchor_ids` bypass semantic anchor lookup.

### Hydration semantics
Hydration is explicit post-selection source recovery (turn/tool/adjacent payloads).
It is not a replacement for retrieval planning.

Deep recall is separate from canonical hydration.

## Adapter entrypoints
- OpenClaw bridge surfaces under `core_memory.integrations.openclaw.*`
- PydanticAI surfaces under `core_memory.integrations.pydanticai.*`
- SpringAI/HTTP surfaces under `core_memory.integrations.http.*` and docs contract
- LangChain surfaces under `core_memory.integrations.langchain.*`

## Compatibility / non-primary
- Archived historical docs and migration artifacts under `docs/archive/` and `docs/reports/`
- Non-canonical helper modules may remain in-tree but are not forward contract unless listed above
