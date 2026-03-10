# Public Surface (Pre-OSS)

Status: Canonical

This is the supported import/integration surface for contributors.

## Runtime/write ingress
- `core_memory.memory_engine`
- `core_memory.integrations.api.emit_turn_finalized(...)`
- `core_memory.event_ingress` (canonical ingress surface alias)
- `core_memory.event_worker` (canonical worker surface alias)
- `core_memory.event_state` (canonical pass-state surface alias)

## Retrieval/runtime tool surface
- `core_memory.tools.memory.get_search_form`
- `core_memory.tools.memory.search`
- `core_memory.tools.memory.execute`
- `core_memory.tools.memory.reason`

## Retrieval schema authority
- `core_memory.retrieval.search_form`

## Launch adapters
- OpenClaw bridge: `core_memory.integrations.openclaw_agent_end_bridge`
- SpringAI: `core_memory.integrations.springai.bridge`
- PydanticAI: `core_memory.integrations.pydanticai.run`

## Not first integration targets (transitional/internal)
- `core_memory.trigger_orchestrator` (compat shim)
- `core_memory.association.pass_engine` (transitional helper)
- compatibility/poller legacy paths

## Rule of thumb
If building new integration code, start from this file and only add new surfaces through explicit docs + tests.
