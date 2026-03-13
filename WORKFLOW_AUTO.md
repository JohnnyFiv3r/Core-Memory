# WORKFLOW_AUTO.md (DEPRECATED)

Status: **Deprecated**
Date: 2026-03-13

This file described legacy automation flows (`extract-beads.py`, root-level script paths, and pre-canonical trigger assumptions).

Do not add new automation here.

## Replacement (canonical)
Use the canonical contract and OpenClaw bridge setup instead:

1. `docs/canonical_contract.md`
2. `docs/integrations/openclaw/plugin-setup.md`
3. `core-memory metrics canonical-health`
4. `core-memory metrics legacy-readiness`

## Why deprecated
- Legacy extraction/consolidation script flow no longer represents canonical runtime ownership.
- Canonical path is now event-driven via:
  - turn path: `memory_engine.process_turn_finalized`
  - flush path: `memory_engine.process_flush`
- Legacy trigger/sidecar compatibility paths are fenced and tracked for removal.

## Migration note
If any external automation still reads this file, migrate it to execute canonical checks/commands only and remove dependency on this document.
