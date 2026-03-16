# OpenClaw Bridge OSS Hardening Plan — 2026-03-16

## Goal
Make bridge install and runtime behavior deterministic for OSS users so they do not hit local-instance failures we observed (ownership gating, stale config drift, truncated payload ingestion).

## Validation Basis
Cross-checked against OpenClaw docs in runtime:
- `/app/docs/tools/plugin.md`
- `/app/docs/cli/plugins.md`
- `/app/docs/plugins/manifest.md`
- `/app/docs/gateway/security/index.md`

## Observed Failure Classes
1. **Plugin ownership gate failures** (`blocked plugin candidate: suspicious ownership`)
2. **Stale config entry drift** (`plugins.entries.core-memory-bridge: plugin not found ... stale config entry ignored`)
3. **Payload truncation in bridge stdin parsing** (`JSONDecodeError: Unterminated string ...`)
4. **Non-deterministic operator procedures** (manual sequences differed per environment)

## Execution Plan

### Slice 1 — Installer + Doctor (in progress)
- Add idempotent install script that:
  - uninstalls prior plugin state via CLI
  - installs plugin from source path
  - normalizes ownership/perms on install directory
  - removes stale `plugins.entries.core-memory-bridge`
  - ensures `plugins.allow` contains `core-memory-bridge`
  - enables plugin
- Add doctor script that checks:
  - plugin listed as loaded
  - stale entries absent
  - no recent blocked/stale warnings
  - hook log presence
  - memory event file presence

### Slice 2 — Documentation hardening
- Add an operator runbook with one canonical path for:
  - Docker Compose setups
  - direct host setups
- Clarify verification signals:
  - plugin list + logs + event files are canonical
  - `hooks list` is not a definitive check for `api.on(...)` typed listeners

### Slice 3 — CI smoke gate
- Add fresh-state smoke test that validates:
  - install succeeds from clean state
  - plugin appears loaded
  - no blocked/stale warnings
  - synthetic turn path appends to `.beads/events/memory-events.jsonl` and pass-status

### Slice 4 — Release + migration hygiene
- Include script usage in release notes.
- Add migration note for stdin full-read bridge fix.
- Keep scripts idempotent so users can rerun safely after updates.

## Work Started (this session)
1. Added `scripts/openclaw_bridge_install.sh` (idempotent installer + config patch)
2. Added `scripts/openclaw_bridge_doctor.sh` (post-install checks)
3. Updated plugin manifest schema to include `coreMemoryRepo`:
   - `plugins/openclaw-core-memory-bridge/openclaw.plugin.json`

## Remaining Tasks
- Add docs section in `README.md` + plugin-specific install/verify examples.
- Add CI smoke job and fixtures.
- Validate scripts in both containerized and bare-metal paths.

## Acceptance Criteria
- Clean install from repo path requires no manual config surgery.
- Re-running installer is safe and converges to same state.
- Doctor script returns zero on healthy setup and clear failures otherwise.
- Fresh normal chat turn causes hook log activity and event append progression.
