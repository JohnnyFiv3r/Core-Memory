# Neo4j Shadow Adapter Scope (PR42 Guardrail)

Neo4j integration in Core Memory is **projection-only**.

## Authority boundary

Neo4j must **not** become authoritative for:
- canonical writes (`process_turn_finalized`, `process_session_start`, `process_flush`)
- canonical retrieval (`search`, `trace`, `execute`)
- bead/association lifecycle state

Core Memory local storage remains the source of truth.

## Intended use

Neo4j is allowed only for:
- visualization
- graph inspection/debugging
- offline query experiments
- demo/reviewer tooling

## Failure isolation

Neo4j availability/configuration must never block canonical Core Memory runtime behavior.
