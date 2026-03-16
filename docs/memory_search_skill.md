# Memory Search Skill (Typed Tool Boundary)

Status: Canonical
Canonical surfaces: `core_memory.retrieval.tools.memory.*`, typed search contract, unified memory facade
See also:
- `docs/index.md`
- `docs/canonical_surfaces.md`
- `docs/memory_search_agent_playbook.md`

This document describes the tool-oriented boundary for memory retrieval.

## Tool endpoints

- `core_memory.retrieval.tools.memory_search.get_search_form(root=".")`
- `core_memory.retrieval.tools.memory_search.search_typed(submission, root=".", explain=True)`
- `core_memory.retrieval.tools.memory.execute(request, root=".", explain=True)` (unified facade)

## Contract

1. Agent decides to use memory search.
2. Agent reads `get_search_form()` to discover knobs + current catalogs.
3. Agent submits typed form to `search_typed(...)`.
4. Tool snaps values to known vocab and runs deterministic retrieval.
5. Tool returns results + explain payload (snaps, warnings, retrieval debug).

## CLI parity

- `core-memory --root <memory-root> memory form`
- `core-memory --root <memory-root> memory search --typed '<json>' --explain`

## Minimal example

```bash
core-memory --root ./memory memory form

core-memory --root ./memory memory search \
  --typed '{
    "intent":"causal",
    "query_text":"why did we move to candidate-first promotion",
    "topic_keys":["promotion_workflow"],
    "k":10,
    "require_structural":true
  }' \
  --explain
```

## Notes

- The tool snaps incident/topic/type/relation values deterministically.
- Unknown values are handled safely (no crash, no hallucinated IDs).
- Tie breaks are deterministic through underlying hybrid/rerank pipeline.
