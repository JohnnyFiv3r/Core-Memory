# PydanticAI API Reference

Status: Canonical

## Primary integration helper
- `core_memory.integrations.pydanticai.run_with_memory`
- `core_memory.integrations.pydanticai.run_with_memory_sync`
- `core_memory.integrations.pydanticai.flush_session`

## Primary write-path port
- `core_memory.runtime.engine.process_turn_finalized(...)`

Adapter/helper write ingress:
- `core_memory.integrations.api.emit_turn_finalized(...)`

## Transcript hydration ports
- `core_memory.integrations.api.get_turn(turn_id, root=".", session_id=None)`
- `core_memory.integrations.api.get_turn_tools(turn_id, root=".", session_id=None)`
- `core_memory.integrations.api.get_adjacent_turns(turn_id, root=".", session_id=None, before=1, after=1)`
- `core_memory.integrations.api.hydrate_bead_sources(root=".", bead_ids=[...], turn_ids=[...], include_tools=False, before=0, after=0)`

## PydanticAI memory tool factories
- `continuity_prompt(root=".", session_id=None, ensure_session_start=True)`
- `ensure_session_start(root=".", session_id=..., source="pydanticai", max_items=80)`
- `memory_search_tool(root=".")`
- `memory_trace_tool(root=".")`
- `memory_execute_tool(root=".")`
- `get_turn_tool(root=".")`
- `get_turn_tools_tool(root=".")`
- `get_adjacent_turns_tool(root=".")`
- `hydrate_bead_sources_tool(root=".")`

## Runtime gating/flags
- `CORE_MEMORY_ENABLED`
- `CORE_MEMORY_TRANSCRIPT_ARCHIVE`
- `CORE_MEMORY_TRANSCRIPT_HYDRATION`

Behavior:
- disabled core memory: ingest/flush hooks no-op safely
- disabled transcript hydration: hydration calls return no result/disabled payload

## Primary runtime tool surfaces
- `core_memory.retrieval.tools.memory.execute(request, root=".", explain=True)`
- `core_memory.retrieval.tools.memory.search(request, root=".", explain=True)`
- `core_memory.retrieval.tools.memory.trace(query, root=".", k=8, ...)`

### Search tool alias mapping
`memory_search_tool(...)` keeps friendly aliases:
- `type_filter` -> canonical `bead_types`
- `scope` -> canonical `scope`

### Tool payload shape
The PydanticAI tool helpers return compact JSON payloads (not hydrated transcript bodies by default).
Use explicit hydration helpers when full source payloads are required.

### Session-start behavior
- `ensure_session_start(...)` is the explicit adapter-owned session-start boundary.
- `continuity_prompt(..., ensure_session_start=True)` invokes the explicit boundary helper first, then performs a pure continuity read.

## Useful CLI/eval references
- `core-memory memory execute --request ...`
- `eval/memory_execute_eval.py`
- `tests/test_pydanticai_memory_tools.py`

## Compatibility note
If legacy retrieval helpers exist in code for migration/history, they are not part of this forward adapter contract unless listed above.
