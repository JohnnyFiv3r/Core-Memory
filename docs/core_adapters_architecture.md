# Core Adapters Architecture

Status: Canonical

See also:
- `docs/index.md`
- `docs/canonical_surfaces.md`
- `docs/contracts/http_api.v1.json`

## Invariant
Exactly one memory event per finalized top-level user turn.

## Adapter model
All adapters converge on the same core contract:
- write via finalized-turn ingest
- read via canonical retrieval surfaces (`search` / `trace` / `execute`)
- hydrate sources explicitly when fidelity is needed

## Write ingress convergence
- In-process adapters: call `emit_turn_finalized(...)`
- HTTP/service adapters: call `POST /v1/memory/turn-finalized`

## Runtime parity model
Canonical runtime retrieval surfaces:
- `search`
- `trace`
- `execute`

Adapters should not teach deprecated retrieval flows as forward guidance.

## Adapter families
- **OpenClaw**: native environment, bridge + runtime integration
- **PydanticAI**: in-process integration helpers + tool factories
- **SpringAI/HTTP**: service bridge contract for JVM-oriented stacks
- **LangChain**:
  - `CoreMemory` (conversation memory / continuity + finalized-turn writeback)
  - `CoreMemoryRetriever` (read-time recall retriever)

## Tenant-aware HTTP behavior
Stateful HTTP memory endpoints support `X-Tenant-Id` and are expected to preserve read/write isolation by tenant.

## Why this architecture works
- one write contract
- one retrieval model
- explicit hydration boundary
- adapter-specific ergonomics without diverging core semantics
