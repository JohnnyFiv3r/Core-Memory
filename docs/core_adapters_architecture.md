# Core Adapters Architecture

Canonical HTTP/API contract artifact:
- `docs/contracts/http_api.v1.json`

## Invariant
Exactly one memory event per finalized top-level user turn.

## Port pattern

- In-process adapters (Python): call `emit_turn_finalized(...)`
- HTTP adapters (JVM/etc): endpoint -> `emit_turn_finalized(...)`

Both paths converge in the same sidecar/event pipeline.

## Runtime skill bridge (Wave 2)

In addition to write ingress, HTTP adapters can call runtime memory tools:
- `POST /v1/memory/execute` (preferred single-call correctness path)
- `GET /v1/memory/search-form`
- `POST /v1/memory/search`
- `POST /v1/memory/reason`
- `POST /v1/memory/classify-intent` (optional pre-call for telemetry/UX, not required)

## Why this works

- No orchestrator-specific storage logic.
- Idempotency keyed by `session_id:turn_id` remains centralized.
- Privacy/backoff/lineage policies remain in core sidecar layer.
- Runtime retrieval/reasoning stays deterministic and inspectable from one HTTP surface.
