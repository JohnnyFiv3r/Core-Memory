# PydanticAI API Reference

Status: Canonical

## Primary integration helper
- `core_memory.integrations.pydanticai.run_with_memory`

## Primary write-path port
- `core_memory.integrations.api.emit_turn_finalized(...)`

## Primary runtime tool surfaces
- `core_memory.tools.memory.execute(request, root="./memory", explain=True)`
- `core_memory.tools.memory.search(form_submission, root="./memory", explain=True)`
- `core_memory.tools.memory.reason(query, root="./memory", k=8, ...)`
- `core_memory.tools.memory.get_search_form(root="./memory")`

## Useful CLI/eval references
- `core-memory memory execute --request ...`
- `eval/memory_execute_eval.py`
- `eval/memory_search_smoke.py`
