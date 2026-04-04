# Neo4j API Reference (Shadow Adapter)

Status: Optional adapter

## Python API

### `neo4j_status(...) -> dict`
Module:
- `core_memory.integrations.neo4j.sync`

Purpose:
- validate adapter status/config/connectivity

Response fields (typical):
- `ok`
- `enabled`
- `status` (`disabled|ready|misconfigured|missing_dependency|connection_failed`)
- `database`
- `nodes`
- `edges`
- `warnings`
- `error` (when non-ok)

### `sync_to_neo4j(root, *, session_id=None, bead_ids=None, prune=False, dry_run=False, config=None) -> dict`
Module:
- `core_memory.integrations.neo4j.sync`

Purpose:
- project local Core Memory beads/associations into Neo4j

Response fields:
- `ok`
- `database`
- `nodes_upserted`
- `edges_upserted`
- `nodes_pruned`
- `edges_pruned`
- `warnings`
- `errors`

Dry-run fields:
- `mode='dry_run'`
- `nodes_planned`
- `edges_planned`

## CLI API

### `core-memory graph neo4j-status`
Options:
- `--strict` (exit code 2 when status is not ok)

### `core-memory graph neo4j-sync`
Options:
- `--session-id <id>`
- `--bead-id <id>` (repeatable)
- `--full`
- `--dry-run`
- `--prune`

## Environment variables
- `CORE_MEMORY_NEO4J_ENABLED`
- `CORE_MEMORY_NEO4J_URI`
- `CORE_MEMORY_NEO4J_USER`
- `CORE_MEMORY_NEO4J_PASSWORD`
- `CORE_MEMORY_NEO4J_DATABASE`
- `CORE_MEMORY_NEO4J_TLS` (optional)
- `CORE_MEMORY_NEO4J_TIMEOUT_MS` (optional)

## Error contract
Common error codes:
- `neo4j_disabled`
- `neo4j_config_error`
- `neo4j_dependency_missing`
- `neo4j_connection_failed`
- `neo4j_sync_failed`

## Ownership marker
Rows written by this adapter include:
- node/relationship property: `cm_owner=core_memory_shadow_v1`

Prune operations are constrained to this marker.

## Scope guardrail
Neo4j is projection-only. It is not a canonical source for write or retrieval authority.
