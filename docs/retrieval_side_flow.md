# Retrieval-side Flow

Status: Canonical

## Entry surfaces
- `core_memory.retrieval.tools.memory.search`
- `core_memory.retrieval.tools.memory.trace`
- `core_memory.retrieval.tools.memory.execute` (preferred unified path)

## Retrieval behavior
1. Canonical request normalization
2. Retrieval over canonical memory surfaces
3. Grounded result assembly with confidence/next_action metadata

## Rules
- Use canonical wrapper surfaces for runtime integrations.
- Compatibility shims are non-canonical and should not be first integration target.
