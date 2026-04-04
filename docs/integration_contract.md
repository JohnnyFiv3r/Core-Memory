# Integration Contract

Status: Canonical

This contract defines what adapters must do today.

## 1) Write contract (required)
All adapters must converge on finalized-turn ingest:
- `emit_turn_finalized(...)`
- or HTTP `POST /v1/memory/turn-finalized`

Required payload (conceptual minimum):
- `session_id`
- `turn_id`
- `user_query`
- `assistant_final`

Required behavior:
- emit once per top-level turn
- idempotent dedupe by `session_id:turn_id`
- fail-open at adapter boundary

## 2) Retrieval contract (canonical)
Forward retrieval family:
- `search`
- `trace`
- `execute`

`execute` is the recommended single-call runtime surface.

## 3) Hydration contract (canonical public)
- `turn_sources`: `cited_turns` | `cited_turns_plus_adjacent`
- `max_beads`
- `adjacent_before`
- `adjacent_after`

Hydration is post-selection source recovery.
Deep recall is separate from canonical hydration.

## 4) HTTP tenant contract
For stateful memory endpoints, tenant scoping via `X-Tenant-Id` must route reads/writes to the same tenant-specific subtree.

Product expectation:
- tenant-scoped writes are visible only to same-tenant reads
- default namespace does not see tenant-scoped memory
- tenant A does not read tenant B

## 5) Adapter classification
- Native finalized-turn adapter
- Bridge adapter (reconstruct + emit finalized-turn)
- Shadow projection adapter (read/export mirror only; non-authoritative)
- Compatibility/historical adapter surfaces (non-forward)

Current adapter families:
- OpenClaw
- PydanticAI
- SpringAI/HTTP
- LangChain
- Neo4j (shadow graph projection)
