# SpringAI API Reference

Status: Canonical
Canonical contract artifact:
- `../../contracts/http_api.v1.json`

## Endpoints

### `GET /healthz`
Returns:
```json
{"ok": true}
```

### `POST /v1/memory/turn-finalized`
Purpose:
- finalized-turn write-path ingestion

Minimum request fields:
- `session_id`
- `turn_id`
- `user_query`
- `assistant_final`

Returns:
```json
{"accepted": true, "event_id": "mev-..."}
```

### `POST /v1/memory/classify-intent`
Purpose:
- optional canonical routing hint

Request:
```json
{"query": "why did promotion inflation happen"}
```

Response includes:
- `intent_class`
- `causal_intent`
- `query_type_bucket`
- `normalized`

### `POST /v1/memory/search`
Purpose:
- canonical retrieval anchor search

Body:
- `root` (optional)
- `request`
- `explain`

Compatibility:
- `form_submission` is accepted as alias, but forward clients should use `request`.

### `POST /v1/memory/execute`
Purpose:
- preferred unified runtime call

Body:
- `root` (optional)
- `request` (MemoryRequest)
- `explain`

Response includes:
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

### `POST /v1/memory/trace`
Purpose:
- canonical causal trace surface

Hydration public contract:
- `turn_sources`: `cited_turns` | `cited_turns_plus_adjacent`
- `max_beads`
- `adjacent_before`
- `adjacent_after`

Notes:
- `cited_turns` disables adjacency (effective 0/0)
- unsupported legacy hydration knobs are ignored

### `POST /v1/memory/session-flush`
Purpose:
- flush pending memory event stream into canonical bead/index projections

### `POST /v1/memory/session-start`
Purpose:
- explicit session-start lifecycle boundary write

Minimum request fields:
- `session_id`

### `GET /v1/memory/continuity`
Purpose:
- runtime continuity injection surface

Optional params:
- `session_id` (read scoping aid only; no write-side mutation)

Notes:
- continuity is pure-read and does not create `session_start` implicitly
- create `session_start` through `POST /v1/memory/session-start`

## Auth
Conditional auth when `CORE_MEMORY_HTTP_TOKEN` is set.

## Tenant behavior
For tenant-isolated deployments, stateful memory endpoints accept `X-Tenant-Id` and scope write/read/flush/continuity to the same tenant namespace.

## Semantic mode transport behavior
For query-based anchor lookup:
- `CORE_MEMORY_CANONICAL_SEMANTIC_MODE=required` and unavailable semantic backend -> HTTP `503` with payload `error.code="semantic_backend_unavailable"`
- `CORE_MEMORY_CANONICAL_SEMANTIC_MODE=degraded_allowed` -> HTTP `200` with explicit `degraded=true` markers

Trace calls with explicit `anchor_ids` do not require semantic anchor lookup.
