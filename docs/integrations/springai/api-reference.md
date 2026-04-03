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

### `GET /v1/memory/search-form` (deprecated compatibility)
Purpose:
- legacy typed-search form compatibility only; not part of the forward recommended surface

### `POST /v1/memory/search`
Purpose:
- typed retrieval/search

Body:
- `root` (optional)
- `form_submission`
- `explain`

### `POST /v1/memory/reason`
Purpose:
- causal reasoning / grounded chain retrieval

Supports pinning:
- `pinned_incident_ids`
- `pinned_topic_keys`
- `pinned_bead_ids`

### `POST /v1/memory/execute`
Purpose:
- preferred unified runtime call

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

### `GET /v1/memory/continuity`
Purpose:
- runtime continuity injection surface

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

## Auth
Conditional auth when `CORE_MEMORY_HTTP_TOKEN` is set.
