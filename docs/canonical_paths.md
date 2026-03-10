# Canonical Paths (Post-P7)

Status: Canonical
Purpose: single reference for primary runtime/data-flow paths.

## Runtime authority
- Primary runtime center: `core_memory/memory_engine.py`
- Primary trigger orchestration: `core_memory/trigger_orchestrator.py`
- OpenClaw-facing adapter: `core_memory/openclaw_integration.py` (wrapper layer)

## Live authority surfaces
- Live session authority: session JSONL (`.beads/session-<id>.jsonl`)
- Index role: projection/cache (`index.json`), rebuildable from authority surfaces

## Continuity surfaces
- Canonical continuity authority: `rolling-window.records.json`
- Fallback metadata surface (non-authoritative): `promoted-context.meta.json`
- Derived/operator artifact (non-authoritative): `promoted-context.md`

Runtime continuity injection authority order:
1. `rolling-window.records.json`
2. `promoted-context.meta.json` fallback only
3. empty (`authority=none`)

## Retrieval primary modules
- Search form schema authority: `core_memory/retrieval/search_form.py`
  - canonical ids: `SEARCH_FORM_SCHEMA_VERSION`, `SEARCH_FORM_TOOL_ID`
- Runtime typed-search surface: `core_memory/tools/memory.py::{get_search_form,search,execute}`
- Compatibility shims (non-canonical runtime entry):
  - `core_memory/tools/memory_search.py`

## Integration framing
- SpringAI primary bridge: `core_memory.integrations.springai.get_app()`
- HTTP compatibility ingress: `core_memory.integrations.http.get_app()`

## Compatibility/deprecated notes
- transcript index-dump write path is retired as a supported primary architecture
- transcript inputs are supported only as bridge/feed into canonical finalized-turn ingestion
- legacy poller path is compatibility-only and hard-fenced by env flag
