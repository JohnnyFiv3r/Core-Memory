# Memory Search Skill (Typed Tool Boundary)

This branch introduces a tool-oriented boundary for memory retrieval.

## Tool endpoints

- `core_memory.tools.memory_search.get_search_form(root="./memory")`
- `core_memory.tools.memory_search.search_typed(submission, root="./memory", explain=True)`

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
