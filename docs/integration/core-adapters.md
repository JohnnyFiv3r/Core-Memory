# Core Adapters (Wave 2)

This document describes the thin adapter surface for non-OpenClaw orchestrators.

Canonical HTTP/API contract artifact:
- `docs/contracts/http_api.v1.json`

## Stable Python port

Use `core_memory.integrations.api.emit_turn_finalized(...)` for write-path ingestion.

## PydanticAI (in-process)

```python
from core_memory.integrations.pydanticai import run_with_memory

result = await run_with_memory(
    agent,
    "user query",
    root="./memory",
    session_id="session-1",
)
```

## SpringAI (JVM -> HTTP)

Run server:

```bash
python3 -m core_memory.integrations.http.server
```

### Write path (async, non-blocking)

`POST /v1/memory/turn-finalized`

Response:

```json
{"accepted": true, "event_id": "mev-..."}
```

### Runtime tool path (sync)

- `POST /v1/memory/classify-intent` (optional pre-call; telemetry/UX only)
- `GET /v1/memory/search-form`
- `POST /v1/memory/search`
- `POST /v1/memory/reason`
- `POST /v1/memory/execute` (preferred single-call correctness path)

## Notes

- `/v1/memory/execute` is the preferred SpringAI runtime endpoint.
- `/v1/memory/classify-intent` is optional and not required for correctness.
- When `CORE_MEMORY_HTTP_TOKEN` is set, auth is required.
