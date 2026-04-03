# Public Surface

Status: Canonical

This page defines what external integrators should call.

## Canonical decision rule
A surface is canonical only if it is both:
1. tested as active contract, and
2. documented as forward-supported in current docs.

## Write ingress
- `core_memory.integrations.api.emit_turn_finalized(...)`

## Retrieval/runtime surface
- `core_memory.tools.memory.search(form_submission, root='.', explain=...)`
- `core_memory.tools.memory.trace(query='', anchor_ids=[...], root='.', k=..., hydration=...)`
- `core_memory.tools.memory.execute(request, root='.', explain=...)`

### Retrieval semantics
- `search`: anchor retrieval
- `trace`: causal traversal/grounding after anchor identification
- `execute`: unified orchestration entrypoint

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
