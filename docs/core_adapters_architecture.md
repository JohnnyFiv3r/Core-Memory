# Core Adapters Architecture

## Invariant
Exactly one memory event per finalized top-level user turn.

## Port pattern

- In-process adapters (Python): call `emit_turn_finalized(...)`
- HTTP adapters (JVM/etc): endpoint -> `emit_turn_finalized(...)`

Both paths converge in the same sidecar/event pipeline.

## Why this works

- No orchestrator-specific storage logic.
- Idempotency keyed by `session_id:turn_id` remains centralized.
- Privacy/backoff/lineage policies remain in core sidecar layer.

## Wave 1 boundaries

Supported now:
- OpenClaw (native)
- PydanticAI adapter
- SpringAI via HTTP ingress

Not in Wave 1:
- streaming memory hooks
- deep tool trace parity
- external queue/database transport
