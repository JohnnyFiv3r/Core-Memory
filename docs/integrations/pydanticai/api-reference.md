# PydanticAI API Reference

Status: Canonical

## Primary integration helper
- `core_memory.integrations.pydanticai.run_with_memory`
- `core_memory.integrations.pydanticai.run_with_memory_sync`
- `core_memory.integrations.pydanticai.flush_session`

## Primary write-path port
- `core_memory.integrations.api.emit_turn_finalized(...)`

## Transcript hydration ports
- `core_memory.integrations.api.get_turn(turn_id, root=".", session_id=None)`
- `core_memory.integrations.api.get_turn_tools(turn_id, root=".", session_id=None)`
- `core_memory.integrations.api.get_adjacent_turns(turn_id, root=".", session_id=None, before=1, after=1)`
- `core_memory.integrations.api.hydrate_bead_sources(root=".", bead_ids=[...], turn_ids=[...], include_tools=False, before=0, after=0)`

## PydanticAI memory tool factories
- `continuity_prompt(root=".")`
- `memory_search_tool(root=".")`
- `memory_reason_tool(root=".")`
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
- `core_memory.retrieval.tools.memory.search(form_submission, root=".", explain=True)`
- `core_memory.retrieval.tools.memory.reason(query, root=".", k=8, ...)`
- `core_memory.retrieval.tools.memory.get_search_form(root=".")`

## Useful CLI/eval references
- `core-memory memory execute --request ...`
- `eval/memory_execute_eval.py`
- `eval/memory_search_smoke.py`
