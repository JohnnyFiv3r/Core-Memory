# Canonical Surfaces

Status: Canonical

Purpose: single answer to “what is real and supported today?”

## Canonical runtime surfaces

### Finalized-turn ingestion (write path)
- `core_memory.integrations.api.emit_turn_finalized(...)`

### Session-start boundary (write path)
- `core_memory.runtime.engine.process_session_start(...)`

### Canonical retrieval family (read/runtime path)
- `core_memory.retrieval.tools.memory.search`
- `core_memory.retrieval.tools.memory.trace`
- `core_memory.retrieval.tools.memory.execute`

Canonical retrieval story is exactly: **search → trace → execute**.

### Continuity surface
- `core_memory.write_pipeline.continuity_injection.load_continuity_injection(...)`

Continuity reads are pure-read by contract (no implicit bead writes).

Continuity authority order:
1. `rolling-window.records.json`
2. `promoted-context.meta.json` (fallback metadata)
3. empty

## Canonical hydration contract

Hydration is post-selection transcript/source recovery, not a general retrieval mode.

Public canonical hydration fields:
- `turn_sources`: `cited_turns` | `cited_turns_plus_adjacent`
- `max_beads`
- `adjacent_before`
- `adjacent_after`

Notes:
- `cited_turns` means cited turns only (adjacency off)
- `cited_turns_plus_adjacent` means cited turns plus bounded neighbors
- unsupported legacy hydration knobs are ignored

Deep recall exists, but it is separate from canonical hydration.

## Canonical HTTP surfaces

Served by `core_memory.integrations.http.server`:
- `GET /healthz`
- `POST /v1/memory/turn-finalized`
- `POST /v1/memory/session-start`
- `POST /v1/memory/session-flush`
- `POST /v1/memory/classify-intent`
- `POST /v1/memory/search`
- `POST /v1/memory/trace`
- `POST /v1/memory/execute`
- `GET /v1/memory/continuity`
- `GET /v1/metrics`

Machine-readable contract:
- `docs/contracts/http_api.v1.json`

## Canonical CLI surfaces
- `core-memory memory search --query ...`
- `core-memory memory trace --query ...`
- `core-memory memory execute --request ...`

## Adapter docs (canonical)
- `docs/integrations/openclaw/README.md`
- `docs/integrations/pydanticai/README.md`
- `docs/integrations/springai/README.md`
- `docs/integrations/langchain/README.md`

Optional shadow-adapter docs (non-canonical runtime authority):
- `docs/integrations/neo4j/README.md`

## Compatibility and historical notes
- Compatibility/historical material lives under `docs/archive/` and `docs/reports/`.
- If older modules still exist in code (for migration/history), they are not forward product surfaces unless listed above.

## Experimental notes
- Experimental helpers/evals may exist under `eval/` and selected adapter experiments.
- Experimental status does not imply canonical runtime contract.
