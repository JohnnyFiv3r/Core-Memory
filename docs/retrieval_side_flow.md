# Retrieval-side Flow

Status: Canonical

## Entry surfaces
- `core_memory.tools.memory.get_search_form`
- `core_memory.tools.memory.search`
- `core_memory.tools.memory.execute` (preferred unified path)
- `core_memory.tools.memory.reason`

## Schema authority
- `core_memory.retrieval.search_form` owns search-form schema identifiers.

## Retrieval behavior
1. Typed form/request normalization
2. Retrieval over canonical memory surfaces
3. Grounded result assembly with confidence/next_action metadata

## Rules
- Use canonical wrapper surfaces for runtime integrations.
- Compatibility shims are non-canonical and should not be first integration target.
