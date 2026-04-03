# Canonical Paths

Status: Canonical
Purpose: single reference for primary runtime/data-flow paths.

## Runtime authority
- Runtime center: `core_memory/memory_engine.py`
- Finalized-turn ingress authority: `core_memory/integrations/api.py::emit_turn_finalized`

## Live authority surfaces
- Session authority: `.beads/session-<id>.jsonl`
- Event authority: `.beads/events/*.jsonl`
- Index role: `.beads/index.json` (projection/cache, rebuildable)

## Continuity authority
1. `rolling-window.records.json` (authoritative)
2. `promoted-context.meta.json` (fallback metadata)
3. `promoted-context.md` (derived operator artifact)

## Retrieval authority
- Planner authority: `core_memory/retrieval/pipeline/canonical.py`
- Public runtime surface: `core_memory/retrieval/tools/memory.py::{search,trace,execute}`

## Hydration authority
- Transcript/turn hydration helpers in `core_memory/integrations/api.py`
- Hydration is explicit post-selection source recovery

## Integration framing
- OpenClaw: in-process bridge-first adapter
- PydanticAI: in-process adapter
- SpringAI/HTTP: service bridge adapters
- LangChain: BaseMemory + BaseRetriever adapter surfaces

## Compatibility / historical
Legacy/deprecated documents and migration process notes are intentionally separated under `docs/archive/` and `docs/reports/`.
