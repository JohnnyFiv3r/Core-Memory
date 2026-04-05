# OpenClaw Integration Guide

Status: Canonical
Canonical surfaces:
- `emit_turn_finalized(...)`
- `get_turn(...)`
- `get_turn_tools(...)`
- `get_adjacent_turns(...)`
- `hydrate_bead_sources(...)`
- `memory.execute`
- `memory.search`
- `memory.trace`

## Architecture
OpenClaw is the native/original environment where Core Memory runs in-process with the main agent runtime.

Core pieces:
1. finalized-turn write-path ingestion
2. memory sidecar / processing pipeline
3. runtime memory skill surface
4. eval/validation harnesses

## Write path
Canonical write port:
- `core_memory.integrations.api.emit_turn_finalized(...)`

Turn-finalized ingest now writes both:
- structured memory event/bead pipeline artifacts
- raw authoritative turn archive records in `.turns/session-<id>.jsonl` (+ per-session index)

Bridge compatibility contract:
- adapter concept: `agent_end`
- OpenClaw/Core Memory runtime equivalent: `turn-finalized`

OpenClaw’s finalized-turn handling should converge here so exactly one deterministic memory event is emitted per top-level user turn.

Decision lock (V2P11):
- transcript/index-dump extraction is not a supported primary write architecture
- transcript-derived workflows are bridge-only and must feed canonical finalized-turn ingestion

## Runtime path
Canonical runtime surface:
- `core_memory.retrieval.tools.memory.execute`

OpenClaw can also access lower-level runtime operations:
- `memory.search`
- `memory.trace`

Forward retrieval story is search/trace/execute. Deprecated retrieval entrypoints are not part of the active guidance.

Hydration path (explicit, non-default):
- retrieve beads/causal structure first
- hydrate full turn payloads only when needed (`get_turn` / `hydrate_bead_sources`)
- fetch tools/adjacent turns only when requested

Deep recall is separate from canonical hydration.

Migration helpers (existing stores):
- `core_memory.integrations.migration.rebuild_turn_indexes(root=...)`
- `core_memory.integrations.migration.backfill_bead_session_ids(root=...)`

Recommended order:
1. rebuild per-session turn indexes from `.turns/*.jsonl`
2. backfill missing bead `session_id` from `source_turn_ids`
3. enable strict/session invariants in staging before production rollout

## Source hierarchy
OpenClaw is uniquely positioned to access:
- transcript/recent session context
- structured memory graph
- archived memory artifacts

Policy guideline:
- same-session recent recall may use transcript-first
- durable/cross-session memory should prefer Core Memory archive-graph-oriented surfaces (`memory.execute`)
- rolling window is continuity-first, not canonical specificity source
- continuity authority is `rolling-window.records.json`; `promoted-context.meta.json` and `promoted-context.md` are fallback/derived only

## Config and models
The OpenClaw runtime selects models and allowlists through OpenClaw config, not Core Memory itself. Core Memory relies on those runtime selections for agent execution and reasoning behavior.

## Runtime flags (OpenClaw supersession controls)
Core flags used by integration bridges and hydration/archive surfaces:

- `CORE_MEMORY_ENABLED` (default `1`)
- `CORE_MEMORY_TRANSCRIPT_ARCHIVE` (default `1`)
- `CORE_MEMORY_TRANSCRIPT_HYDRATION` (default `1`)
- `CORE_MEMORY_SUPERSEDE_OPENCLAW_SUMMARY` (default `0`)
- `CORE_MEMORY_SOUL_PROMOTION` (default `0`)
- `CORE_MEMORY_DEFAULT_HYDRATE_TOOLS` (default `0`)
- `CORE_MEMORY_DEFAULT_ADJACENT_TURNS` (default `0`)

Operational policy:
- When Core Memory supersession is enabled, OpenClaw generic summary/session-memory should be bypassed.
- SOUL.md remains the durable preference/identity surface and is not superseded by episodic Core Memory storage.

## Current docs to consult
- `../../canonical_surfaces.md`
- `../../core_adapters_architecture.md`
