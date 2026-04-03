# Public Surface (Pre-OSS)

Status: Canonical

This is the supported import/integration surface for contributors.

## Canonical decision rule
A function/module is canonical only if:
1) it has tests, and
2) it is exported via package surface (`core_memory.__init__` or integration API surface such as `core_memory.integrations.api`).

Everything else is internal and may change without notice.

## Runtime/write ingress
- `core_memory.integrations.api.emit_turn_finalized(payload: dict) -> dict` — canonical finalized-turn ingress.
- `core_memory.event_ingress` — canonical ingress alias module.
- `core_memory.event_worker` — canonical worker alias module.
- `core_memory.event_state` — canonical pass-state alias module.

## Retrieval/runtime tool surface
- `core_memory.tools.memory.search(form_submission: dict, root: str='.', explain: bool=False) -> dict` — canonical retrieval anchors.
- `core_memory.tools.memory.trace(query: str, root: str='.', k: int=8, ...) -> dict` — canonical causal traversal after anchor identification.
- `core_memory.tools.memory.execute(root: str, request: dict, explain: bool=False) -> dict` — unified memory request entrypoint.

### Semantic mode contract
- Query-based anchor lookup uses semantic backend by default.
- If semantic backend is unavailable and `CORE_MEMORY_CANONICAL_SEMANTIC_MODE=required`, payloads return:
  - `ok=false`
  - `error.code="semantic_backend_unavailable"`
  - `degraded=false`
- In `degraded_allowed` mode, payloads remain `ok=true` with explicit `degraded=true` + degradation warnings.

Trace calls with explicit `anchor_ids` bypass semantic anchor lookup.

## Launch adapters
- OpenClaw bridge: `core_memory.integrations.openclaw_agent_end_bridge`
- SpringAI: `core_memory.integrations.springai.bridge`
- PydanticAI: `core_memory.integrations.pydanticai.run`
- LangChain: `core_memory.integrations.langchain.{CoreMemory, CoreMemoryRetriever}`

## Non-primary surfaces
Association preview helper (non-authoritative write preview only):
- `core_memory.association.preview.run_association_pass`

## Rule of thumb
If building new integration code, start from this file and only add new surfaces through explicit docs + tests.
