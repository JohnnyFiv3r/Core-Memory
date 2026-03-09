# V2-P5 Integration Inventory (As-Is -> Target)

Status: Step 1 artifact
Purpose: classify integration surfaces and define target framing posture.

## Surface inventory

### 1) Core runtime center
- Path: `core_memory/memory_engine.py`
- Current role: canonical runtime center entrypoint
- Classification: **canonical**
- Target: remain primary runtime orchestration surface

### 2) OpenClaw integration facade
- Path: `core_memory/openclaw_integration.py`
- Current role: OpenClaw-facing helper/facade, includes legacy poller compatibility path
- Classification: **canonical facade + legacy compat branch**
- Target: keep facade, retain explicit legacy markers for poller compatibility path

### 3) Sidecar hook / worker paths
- Paths:
  - `core_memory/sidecar_hook.py`
  - `core_memory/sidecar_worker.py`
  - `core_memory/sidecar.py`
- Current role: historical event processing + compatibility helper behavior
- Classification: **compatibility/legacy transition**
- Target: preserve compatibility where needed, prevent authority drift from canonical engine

### 4) Integration API port
- Path: `core_memory/integrations/api.py`
- Current role: stable integration port (`emit_turn_finalized*`)
- Classification: **canonical integration port**
- Target: keep stable API semantics, clarify docs around canonical trigger route

### 5) HTTP ingress surfaces
- Path family: `core_memory/integrations/http/*` (when present)
- Current role: generic runtime ingress utilities
- Classification: **generic integration surface**
- Target: SpringAI bridge framing in docs/module posture while preserving compatibility

### 6) Tool adapter shim
- Path: `core_memory/tools/memory.py`
- Current role: adapter shim over canonical runtime/tool logic
- Classification: **adapter (not semantics owner)**
- Target: keep explicit adapter status, prevent semantic-policy creep

## As-Is -> Target framing deltas

1. Clarify SpringAI bridge-first framing in integration docs without breaking generic utility code.
2. Keep `memory_engine.py` as clear architectural center in docs and references.
3. Strengthen explicit labels for legacy compatibility paths (sidecar/poller branch).
4. Ensure deprecation inventory has per-path statuses (active/deprecated/removed) with phase targets.

## Proposed Step 2 low-risk changes
- Documentation updates in integration guides and architecture docs.
- Optional module-level docstring reframing (no endpoint/path breakage).
- No functional behavior changes unless needed for canonical-path assertions.
