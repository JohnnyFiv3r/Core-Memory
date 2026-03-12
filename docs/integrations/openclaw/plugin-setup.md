# OpenClaw Plugin Setup (Core Memory Bridge)

Status: Canonical

## Goal
Activate Core Memory lifecycle listeners in OpenClaw:
- `agent_end` -> finalized-turn ingestion (`emit_turn_finalized` via bridge)
- `before_compaction` / `after_compaction` -> flush processor (`process_flush`)

This is required if you want Core Memory event/bead pipelines active in a live OpenClaw runtime.

## Coexist vs Replace
You can run in two modes:

1. **Coexist (recommended first):**
   - Keep OpenClaw `memory-core` plugin enabled
   - Add `core-memory-bridge` plugin for event/flush routing

2. **Replace:**
   - Enable `core-memory-bridge`
   - Disable stock `memory-core`

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

## What onboarding does
- Installs plugin from `plugins/openclaw-core-memory-bridge`
- Enables plugin id `core-memory-bridge`
- Keeps `memory-core` enabled (coexist mode) or disables it (replace mode)
- Runs `openclaw status --deep` for immediate verification

## Verification checklist
1. `openclaw plugins list` shows `core-memory-bridge` as loaded.
2. `openclaw status --deep` is healthy.
3. After a turn, Core Memory event files under `$CORE_MEMORY_ROOT/.beads/events/` update.
4. During compaction cycles, flush checkpoints update.

## Notes
- Bridge shell-outs to Python modules:
  - `core_memory.integrations.openclaw_agent_end_bridge`
  - `core_memory.integrations.openclaw_compaction_bridge`
- `CORE_MEMORY_ROOT` controls where Core Memory durable artifacts are written.
