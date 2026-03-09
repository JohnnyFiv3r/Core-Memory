# V2-P7C Shim Inventory

Status: Step 1 artifact

## Deprecated compatibility shims

1. `core_memory.memory_skill.form`
- Replacement: `core_memory.retrieval.search_form`
- Marker: `LEGACY_SHIM=True`

2. `core_memory.write_pipeline.window`
- Replacement: `core_memory.rolling_surface`
- Marker: `LEGACY_SHIM=True`

## Non-authoritative compatibility path (hard-fenced)

3. `core_memory.openclaw_integration.process_pending_memory_events`
- Status: compatibility only, disabled by default
- Enable flag: `CORE_MEMORY_ENABLE_LEGACY_POLLER=1`
