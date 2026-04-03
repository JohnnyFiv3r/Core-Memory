# Shared Contracts

Status: Canonical

Primary machine-readable contract:
- `../../contracts/http_api.v1.json`

## Contract hierarchy

### HTTP runtime contract
For remote/JVM integrations, use the HTTP contract artifact as source of truth.

### Tool/runtime contract
Canonical runtime surfaces:
- `core_memory.tools.memory.execute`
- `core_memory.tools.memory.search`
- `core_memory.tools.memory.trace`

### Write-path contract
Canonical finalized-turn ingestion:
- `core_memory.integrations.api.emit_turn_finalized(...)`
- `POST /v1/memory/turn-finalized`

## Request/response invariants

### MemoryRequest / memory.execute
The unified request object should support:
- `raw_query`
- `intent`
- `constraints.require_structural`
- `facets.incident_ids`
- `facets.topic_keys`
- `facets.bead_types`
- `facets.relation_types`
- `facets.pinned_bead_ids`
- `facets.must_terms`
- `facets.avoid_terms`
- `facets.time_range`
- `k`

### MemoryResponse
The unified response should provide:
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

## Auth contract
If `CORE_MEMORY_HTTP_TOKEN` is configured, HTTP callers must provide:
- `Authorization: Bearer <token>`
- or `X-Memory-Token: <token>`
