# PydanticAI Quickstart

Status: Canonical
See also:
- `README.md`
- `integration-guide.md`
- `../shared/concepts.md`

## Goal
Run Core Memory in-process with a PydanticAI-based agent.

## 1) Install

Base install (stub-friendly examples only):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Real PydanticAI agent usage requires the adapter extra:

```bash
pip install -e ".[pydanticai]"
```

## 2) Use the integration helper
```python
from core_memory.integrations.pydanticai import run_with_memory

result = await run_with_memory(
    agent,
    "user query",
    root=".",
    session_id="session-1",
)
```

## 3) Runtime memory usage
PydanticAI can use the runtime memory tools directly in-process:
- `core_memory.retrieval.tools.memory.execute`
- `core_memory.retrieval.tools.memory.search`
- `core_memory.retrieval.tools.memory.trace`

Practical rule:
- use `execute` as the default deterministic entrypoint
- use `search` / `trace` directly when your agent policy needs explicit control

## 4) Validate
```bash
python -m unittest tests.test_memory_search_tool_wrapper
python -m unittest tests.test_pydanticai_memory_tools
python eval/memory_execute_eval.py
```
