# OpenClaw API Reference

Status: Canonical

## Primary runtime code surfaces
- `core_memory.retrieval.tools.memory.execute(request, root=".", explain=True)`
- `core_memory.retrieval.tools.memory.search(form_submission, root=".", explain=True)`
- `core_memory.retrieval.tools.memory.trace(query, root=".", k=8, ...)`

## Primary write-path surface
- `core_memory.integrations.api.emit_turn_finalized(...)`

## Transcript hydration surfaces
- `core_memory.integrations.api.get_turn(turn_id, root=".", session_id=None)`
- `core_memory.integrations.api.get_turn_tools(turn_id, root=".", session_id=None)`
- `core_memory.integrations.api.get_adjacent_turns(turn_id, root=".", session_id=None, before=1, after=1)`
- `core_memory.integrations.api.hydrate_bead_sources(root=".", bead_ids=[...], turn_ids=[...], include_tools=False, before=0, after=0)`

Notes:
- Hydration APIs are gated by `CORE_MEMORY_TRANSCRIPT_HYDRATION`.
- Turn archive writes are gated by `CORE_MEMORY_TRANSCRIPT_ARCHIVE`.
- `hydrate_bead_sources` is a convenience path that resolves bead `source_turn_ids` to full turn payloads.

## CLI surfaces
- `core-memory memory search --query ...`
- `core-memory memory trace --query ...`
- `core-memory memory execute --request ...`
- `core-memory graph ...`
- `core-memory metrics ...`

## Validation/eval surfaces
- `eval/memory_execute_eval.py`
- `tests/test_openclaw_read_bridge.py`
