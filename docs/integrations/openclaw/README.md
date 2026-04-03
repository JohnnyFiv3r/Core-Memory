# OpenClaw Integration Docs

Status: Canonical landing page

## Operator quick path (recommended)
- Install/harden bridge: `../../../scripts/openclaw_bridge_install.sh`
- Verify health: `../../../scripts/openclaw_bridge_doctor.sh`
- Synthetic append smoke gate: `../../../scripts/openclaw_bridge_ci_smoke.sh`

## Canonical sources
- `plugin-setup.md`
- `validation.md`
- `troubleshooting.md`
- `agent_end_bridge.md`
- `api-reference.md`
- `../../canonical_surfaces.md`
- repository root `README.md`

## Operational notes
- Forward retrieval story is `search` / `trace` / `execute` (`execute` preferred by default policy).
- Hydration is optional post-selection source recovery, not the primary retrieval mode.
- For plugin `api.on(...)` lifecycle listeners, do **not** rely solely on `openclaw hooks list --json`.
- Primary runtime signals are:
  - `/tmp/core-memory-bridge-hook.log` movement
  - `.beads/events/memory-events.jsonl` append progression
  - `.beads/events/memory-pass-status.jsonl` append progression
  - absence of blocked/stale plugin warnings
