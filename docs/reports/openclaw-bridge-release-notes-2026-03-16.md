# Core Memory OpenClaw Bridge — Release/Migration Notes (2026-03-16)

Status: Canonical for this hardening slice

## Summary
This slice ships deterministic OSS install/verification paths to prevent common local-instance failures observed during bridge adoption:
- suspicious ownership load-blocks
- stale plugin config entry drift
- truncated stdin JSON ingestion under large runtime payloads

## What shipped
- Idempotent installer: `scripts/openclaw_bridge_install.sh`
- Post-install doctor: `scripts/openclaw_bridge_doctor.sh`
- Synthetic CI smoke gate: `scripts/openclaw_bridge_ci_smoke.sh`
- CI workflow: `.github/workflows/openclaw-bridge-smoke.yml`
- Plugin schema update: `coreMemoryRepo` added to bridge manifest
- OpenClaw docs updated with canonical runbooks + verification semantics

## Migration guidance for existing users
1. Pull latest repository changes.
2. Run install script (prefer root where ownership normalization is required).
3. Restart OpenClaw runtime/container.
4. Run doctor script.
5. Trigger one normal chat turn and validate event append progression.

## Verification criteria
Healthy bridge state should show:
- `core-memory-bridge` listed as loaded
- no recent blocked/stale plugin warnings
- hook log activity in `/tmp/core-memory-bridge-hook.log`
- append progression in:
  - `.beads/events/memory-events.jsonl`
  - `.beads/events/memory-pass-status.jsonl`

## Known operational note
`openclaw hooks list --json` is not a complete liveness check for plugin typed listeners registered through `api.on(...)`; rely on runtime append signals above.
