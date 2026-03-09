# V2 Legacy Resolution Summary

Status: Snapshot (P5 closeout)

## Canonical active surfaces
- `core_memory/memory_engine.py`
- `core_memory/trigger_orchestrator.py`
- `core_memory/integrations/api.py`
- `core_memory/integrations/springai/bridge.py`

## Compatibility-only surfaces (non-authoritative)
- `core_memory/openclaw_integration.py::process_pending_memory_events` (hard-fenced; disabled by default)
- `core_memory/sidecar_hook.py`
- `core_memory/sidecar_worker.py`

## Deprecated authority semantics
- Sidecar-led authority assumptions
- Duplicate wrapper authority routes
- Pre-v2 execution documents used as active implementation source

## Marker policy
- canonical: `authority_path=canonical_in_process`
- compatibility legacy: `authority_path=legacy_sidecar_compat`
