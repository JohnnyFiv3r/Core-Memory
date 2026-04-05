# PydanticAI Integration Guide

Status: Canonical
Canonical surfaces:
- `run_with_memory(...)`
- `run_with_memory_sync(...)`
- `flush_session(...)`
- `process_turn_finalized(...)`
- `emit_turn_finalized(...)` (adapter/helper ingress)
- `get_turn(...)`
- `get_turn_tools(...)`
- `get_adjacent_turns(...)`
- `hydrate_bead_sources(...)`
- `memory.execute`

## Architecture
PydanticAI is the simplest non-OpenClaw integration because it runs in Python and can call Core Memory directly without an HTTP bridge.

Two paths exist:
1. **Write path** — emit finalized-turn memory events
2. **Runtime path** — use memory skill/tool functions in-process
3. **Hydration path** — retrieve raw turn/tool provenance on demand

## Write path
Canonical write boundary:
- `core_memory.runtime.engine.process_turn_finalized(...)`

Adapter/helper write ingress:
- `core_memory.integrations.api.emit_turn_finalized(...)`

Convenience helper:
- `core_memory.integrations.pydanticai.run_with_memory`
- `core_memory.integrations.pydanticai.run_with_memory_sync`

Canonical semantics:
- Exactly one top-level finalized-turn ingest per user turn
- Turn ingest writes structured memory events and (when enabled) raw turn archive records
- Fail-open contract: agent result returns even if memory pipeline errors

Runtime guard:
- `CORE_MEMORY_ENABLED=0` skips pydanticai memory ingest/flush surfaces safely

## Runtime path
Preferred unified runtime surface:
- `core_memory.retrieval.tools.memory.execute`

Session-start boundary helper:
- `ensure_session_start(root=..., session_id=...)`

Optional direct surfaces:
- `memory.search`
- `memory.trace`

Hydration (explicit, non-default):
- `get_turn(turn_id, session_id?)`
- `get_turn_tools(turn_id, session_id?)`
- `get_adjacent_turns(turn_id, session_id?, before, after)`
- `hydrate_bead_sources(bead_ids|turn_ids, include_tools, before, after)`

Design intent:
- Beads answer: "what matters and why"
- Hydrated turns answer: "what exactly happened"
- Tool traces answer: "what evidence/work produced this"

Continuity semantics:
- `continuity_prompt(...)` is prompt-time continuity read context.
- with `ensure_session_start=True`, it first calls explicit adapter-owned session-start boundary helper, then performs pure continuity read.

## Why this path is attractive
- no HTTP bridge required
- no separate auth/token concerns for runtime calls
- direct Python function access
- lower integration overhead than SpringAI
- strong tool ergonomics for explicit policy routing (`execute` default, `search`/`trace` when needed)

## Recommended usage model
- use `run_with_memory(...)` or equivalent finalized-turn emission for writes
- use `memory.execute` as the main runtime retrieval/reasoning facade for durable memory
- use transcript hydration only when fidelity/provenance is needed
- use lower-level memory operations only when you need specialized control

## Runtime flags (PydanticAI-relevant)
- `CORE_MEMORY_ENABLED` (default `1`)
- `CORE_MEMORY_TRANSCRIPT_ARCHIVE` (default `1`)
- `CORE_MEMORY_TRANSCRIPT_HYDRATION` (default `1`)
- `CORE_MEMORY_DEFAULT_HYDRATE_TOOLS` (default `0`)
- `CORE_MEMORY_DEFAULT_ADJACENT_TURNS` (default `0`)

Notes:
- Archive writes are gated by `CORE_MEMORY_TRANSCRIPT_ARCHIVE`.
- Hydration APIs are gated by `CORE_MEMORY_TRANSCRIPT_HYDRATION`.
- PydanticAI adapter metadata includes a `core_memory_flags` snapshot for diagnostics.

## Trace passthrough
`run_with_memory(...)` and `run_with_memory_sync(...)` support optional:
- `tools_trace`
- `mesh_trace`

These traces flow into canonical turn ingestion, enabling transcript/tool provenance parity with bridge-based runtimes.
