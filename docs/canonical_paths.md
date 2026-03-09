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
- Canonical continuity store: `rolling-window.records.json`
- Derived/operator artifact: `promoted-context.md`

## Retrieval primary modules
- Search form primary: `core_memory/retrieval/search_form.py`
- Skill-form shim (deprecated): `core_memory/memory_skill/form.py`

## Integration framing
- SpringAI primary bridge: `core_memory.integrations.springai.get_app()`
- HTTP compatibility ingress: `core_memory.integrations.http.get_app()`

## Compatibility/deprecated notes
- `core_memory.write_pipeline.window` is a compatibility shim (primary owner: `core_memory.rolling_surface`)
- legacy poller path is compatibility-only and hard-fenced by env flag
