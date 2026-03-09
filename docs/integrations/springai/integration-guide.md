# SpringAI Integration Guide

Status: Canonical
Canonical surfaces:
- `POST /v1/memory/turn-finalized`
- `POST /v1/memory/execute`
- `POST /v1/memory/classify-intent` (optional)

## Architecture
SpringAI runs on the JVM and uses Core Memory over HTTP.

Bridge framing:
- SpringAI-first ingress entrypoint: `core_memory.integrations.springai.get_app()`
- Compatibility ingress remains available via: `core_memory.integrations.http.get_app()`

Two paths exist:
1. **Write path** — non-blocking finalized-turn ingestion
2. **Runtime path** — synchronous memory tool calls

Both paths converge into the same deterministic Core Memory runtime.

## Write path
Use `POST /v1/memory/turn-finalized` after the assistant final response is known.

Behavior:
- async / fire-and-forget from Spring side
- idempotent by `session_id:turn_id`
- should never block user response on failure

## Runtime path
Preferred endpoint:
- `POST /v1/memory/execute`

Surface usage policy:
- immediate verbatim recall -> transcript-first behavior on caller side when available
- durable recall/causal/history -> `memory.execute` (archive-graph-oriented path)

Why preferred:
- one canonical MemoryRequest object
- one canonical MemoryResponse shape
- internal orchestration can do typed retrieval, snapping, and causal grounding without JVM-side duplication

Optional endpoint:
- `POST /v1/memory/classify-intent`

Use classify-intent only for:
- telemetry
- UX routing hints
- debugging

Do **not** require a pre-classification call for correctness.

## Request flow
1. SpringAI receives user message
2. optionally classify for UI/telemetry
3. call `memory.execute`
4. answer from `results`, `chains`, `grounding`, `confidence`, `next_action`
5. after final assistant response, emit `turn-finalized`

## Auth
If `CORE_MEMORY_HTTP_TOKEN` is set on the Core Memory service, SpringAI must send either:
- `Authorization: Bearer <token>`
- `X-Memory-Token: <token>`

## Pinning support
The runtime reason path supports pinning fields:
- `pinned_incident_ids`
- `pinned_topic_keys`
- `pinned_bead_ids`

If SpringAI uses a two-step flow (`search -> reason`), these should be passed through to preserve determinism.
