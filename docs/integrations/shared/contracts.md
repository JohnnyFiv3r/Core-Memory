# Shared Contracts

Status: Canonical

Primary machine-readable HTTP contract:
- `../../contracts/http_api.v1.json`

## Canonical runtime contracts

### Write contract
- `emit_turn_finalized(...)`
- HTTP `POST /v1/memory/turn-finalized`

### Retrieval contract
- `search`
- `trace`
- `execute`

### Hydration contract (public canonical)
- `turn_sources`: `cited_turns` | `cited_turns_plus_adjacent`
- `max_beads`
- `adjacent_before`
- `adjacent_after`

## Unified request/response expectations

### MemoryRequest (execute)
Common fields include:
- `raw_query`
- `intent`
- `constraints.require_structural`
- `facets.*` (topic/type/relation/time/pins/terms)
- `k`

### MemoryResponse
Expected fields:
- `ok`
- `request`
- `snapped`
- `results`
- `chains`
- `grounding`
- `confidence`
- `next_action`
- `warnings`
- `explain` (optional)

## Tenant-aware HTTP expectation
Stateful memory endpoints use optional `X-Tenant-Id` to preserve read/write isolation.

## Auth expectation
If `CORE_MEMORY_HTTP_TOKEN` is configured, HTTP callers send either:
- `Authorization: Bearer <token>`
- `X-Memory-Token: <token>`
