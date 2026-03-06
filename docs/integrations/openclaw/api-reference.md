# OpenClaw API Reference

Status: Canonical

## Primary runtime code surfaces
- `core_memory.tools.memory.execute(request, root="./memory", explain=True)`
- `core_memory.tools.memory.search(form_submission, root="./memory", explain=True)`
- `core_memory.tools.memory.reason(query, root="./memory", k=8, ...)`
- `core_memory.tools.memory.get_search_form(root="./memory")`

## Primary write-path surface
- `core_memory.integrations.api.emit_turn_finalized(...)`

## CLI surfaces
- `core-memory memory form`
- `core-memory memory search --typed ...`
- `core-memory memory execute --request ...`
- `core-memory reason <query>`
- `core-memory graph ...`
- `core-memory metrics ...`

## Validation/eval surfaces
- `eval/memory_execute_eval.py`
- `eval/memory_search_ab_compare.py`
- `eval/retrieval_eval.py`
- `eval/paraphrase_eval.py`
