# V2-P5 Legacy Classification

Status: Step 3 artifact
Purpose: explicit canonical/compat/deprecated labeling for remaining transition paths.

## Classification matrix

### Canonical
- `core_memory/memory_engine.py` (runtime center)
- `core_memory/trigger_orchestrator.py` (canonical trigger orchestration)
- `core_memory/integrations/api.py` (stable integration port)
- `core_memory/integrations/springai/bridge.py` (SpringAI bridge framing entrypoint)

### Compatibility (non-authoritative)
- `core_memory/openclaw_integration.py::process_pending_memory_events` (legacy poller compatibility)
- `core_memory/sidecar_hook.py` (compat emit helper)
- `core_memory/sidecar_worker.py` (compat event processor)

### Deprecated (transition-only)
- Sidecar-authority semantics (superseded by canonical in-process authority)
- Wrapper routes that duplicate canonical behavior without adding operational value
- Pre-v2 roadmap references used as active execution guide

## Marker policy
- Canonical paths should declare `authority_path=canonical_in_process` where relevant.
- Compatibility paths should declare `authority_path=legacy_sidecar_compat` where relevant.
- Deprecated docs/components remain until removal phase but are explicitly labeled deprecated.
