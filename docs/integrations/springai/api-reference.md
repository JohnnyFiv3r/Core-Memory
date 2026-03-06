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

### `GET /v1/memory/search-form`
Purpose:
- fetch machine-readable typed search form and current catalogs

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
