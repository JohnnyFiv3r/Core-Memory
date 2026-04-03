# SpringAI Integration Guide

Status: Canonical

Canonical HTTP surfaces:
- `POST /v1/memory/turn-finalized`
- `POST /v1/memory/execute`
- `POST /v1/memory/search`
- `POST /v1/memory/trace`
- `POST /v1/memory/classify-intent` (optional)

## Architecture
SpringAI runs on the JVM and uses Core Memory over HTTP.

Bridge framing:
- SpringAI-first ingress entrypoint: `core_memory.integrations.springai.get_app()`
- Compatibility ingress remains available via: `core_memory.integrations.http.get_app()`

Two paths:
1. write path (non-blocking finalized-turn ingest)
2. runtime path (synchronous retrieval calls)

## Write path
Use `POST /v1/memory/turn-finalized` after assistant final output is known.

Behavior:
- async/fire-and-forget on Spring side
- idempotent by `session_id:turn_id`
- fail-open integration boundary

## Runtime path
Preferred endpoint:
- `POST /v1/memory/execute`

Optional direct retrieval endpoints:
- `POST /v1/memory/search`
- `POST /v1/memory/trace`

Optional routing hint endpoint:
- `POST /v1/memory/classify-intent`

Use classify-intent for telemetry/UX hints only, not correctness.

## Request flow
1. SpringAI receives user message
2. optionally classify for UI/telemetry
3. call `memory.execute`
4. answer from `results/chains/grounding/confidence/next_action`
5. emit `turn-finalized` after assistant final response

## Auth
If `CORE_MEMORY_HTTP_TOKEN` is set, provide either:
- `Authorization: Bearer <token>`
- `X-Memory-Token: <token>`

## Tenant scoping (stateful endpoints)
For tenant-isolated deployments, use `X-Tenant-Id` on stateful memory endpoints so reads and writes remain in the same tenant namespace.
