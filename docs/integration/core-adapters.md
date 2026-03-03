# Core Adapters (Wave 1)

This document describes the thin adapter surface for non-OpenClaw orchestrators.

## Stable Python Port

Use `core_memory.integrations.api.emit_turn_finalized(...)`.

It emits exactly one `TURN_FINALIZED` event and returns `event_id`.

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

## SpringAI (JVM -> HTTP ingress)

Run ingress server:

```bash
python3 -m core_memory.integrations.http_ingress
```

POST:

```http
POST /v1/memory/turn-finalized
Content-Type: application/json

{
  "root": "./memory",
  "session_id": "s1",
  "turn_id": "t1",
  "transaction_id": "tx1",
  "user_query": "...",
  "assistant_final": "...",
  "metadata": {"tenant_id": "acme"}
}
```

Response:

```json
{"ok": true, "event_id": "mev-..."}
```
