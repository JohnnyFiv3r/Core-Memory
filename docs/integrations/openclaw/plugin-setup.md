# OpenClaw Plugin Setup (Core Memory Bridge)

Status: Canonical

## Goal
Activate Core Memory lifecycle listeners in OpenClaw:
- `agent_end` -> finalized-turn ingestion (`emit_turn_finalized` via bridge)
- `memory_search` -> canonical read-path dispatch (`memory.execute` via read bridge) **[enabled by default]**
- `before_compaction` / `after_compaction` -> flush processor (`process_flush`)

This is required if you want Core Memory event/bead pipelines active in a live OpenClaw runtime.

### Memory search priority

`memory_search` hook registration is **on by default**. When registered, the bridge intercepts
OpenClaw's memory search lifecycle and routes it through `core_memory.integrations.openclaw_read_bridge`
(`action=execute`), so Core Memory's semantic retrieval takes priority over OpenClaw's built-in
`memory-core` search.

To opt out (keep OpenClaw's default search while still using Core Memory write/flush paths), set
`enableMemorySearch: false` in the plugin config.

## Coexist vs Replace
You can run in two modes:

1. **Coexist (recommended first):**
   - Keep OpenClaw `memory-core` plugin enabled
   - Add `core-memory-bridge` plugin for event/flush routing

2. **Replace:**
   - Enable `core-memory-bridge`
   - Disable stock `memory-core`

Flag-driven default (recommended for automated deploys):
- `CORE_MEMORY_SUPERSEDE_OPENCLAW_SUMMARY=1` -> onboard defaults to replace mode (disables `memory-core`)
- `CORE_MEMORY_SUPERSEDE_OPENCLAW_SUMMARY=0` -> onboard defaults to coexist mode

## Onboarding command
Use Core Memory CLI:

```bash
core-memory openclaw onboard
```

Optional replace mode:

```bash
core-memory openclaw onboard --replace-memory-core
```

Dry-run preview:

```bash
core-memory openclaw onboard --dry-run
```

## Canonical deterministic install path (recommended)

Prefer scripted install/verification over ad-hoc manual commands:

```bash
export CORE_MEMORY_REPO="$(pwd)"   # repo root
./scripts/openclaw_bridge_install.sh
./scripts/openclaw_bridge_doctor.sh
```

### Docker Compose (root-required ownership normalization)

```bash
docker compose exec --user root openclaw bash -lc '$CORE_MEMORY_REPO/scripts/openclaw_bridge_install.sh'
docker compose restart openclaw
docker compose exec openclaw bash -lc '$CORE_MEMORY_REPO/scripts/openclaw_bridge_doctor.sh'
```

### Bare host

```bash
sudo "$CORE_MEMORY_REPO/scripts/openclaw_bridge_install.sh"
openclaw gateway restart
"$CORE_MEMORY_REPO/scripts/openclaw_bridge_doctor.sh"
```

## What onboarding does
- Installs plugin from `plugins/openclaw-core-memory-bridge`
- Enables plugin id `core-memory-bridge`
- Keeps `memory-core` enabled (coexist mode) or disables it (replace mode)
- Runs `openclaw status --deep` for immediate verification

Bridge/runtime flags are exposed in onboard output under `flags` and reflected in bridge-ingested metadata as `core_memory_flags` for diagnostics.

## Verification checklist
1. `openclaw plugins list` shows `core-memory-bridge` as loaded.
2. `openclaw status --deep` is healthy.
3. After a turn, Core Memory event files under `$CORE_MEMORY_ROOT/.beads/events/` update.
4. During compaction cycles, flush checkpoints update.

Important: `openclaw hooks list --json` is **not** a definitive signal for typed lifecycle listeners registered via plugin `api.on(...)`.
Use runtime signals instead:
- bridge hook log movement (`/tmp/core-memory-bridge-hook.log`)
- append progression in `memory-events.jsonl` and `memory-pass-status.jsonl`
- absence of blocked/stale plugin warnings in recent logs

## Notes
- Bridge shell-outs to Python modules:
  - `core_memory.integrations.openclaw_agent_end_bridge`
  - `core_memory.integrations.openclaw_compaction_bridge`
- `CORE_MEMORY_ROOT` controls where Core Memory durable artifacts are written.
- `CORE_MEMORY_ENABLED=0` cleanly no-ops bridge turn ingest/flush paths (safe rollback switch).
