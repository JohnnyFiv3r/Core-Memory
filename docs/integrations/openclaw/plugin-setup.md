# OpenClaw Plugin Setup (Core Memory Bridge)

Status: Canonical

## Goal
Activate Core Memory lifecycle listeners in OpenClaw:
- `agent_end` -> finalized-turn ingestion (`emit_turn_finalized` via bridge)
- `message_received` + `message_sent` -> finalized-turn fallback for streaming channels that do not dispatch `agent_end`
- `memory_search` -> canonical read-path dispatch (`memory.execute` via read bridge) **[enabled by default]**
- `before_compaction` / `after_compaction` -> flush processor (`process_flush`)

This is required if you want Core Memory event/bead pipelines active in a live OpenClaw runtime.

### Memory search priority

`memory_search` hook registration is **on by default**. When registered, the bridge intercepts
OpenClaw's memory search lifecycle and routes it through `core_memory.integrations.openclaw.read_bridge`
(`action=execute`), so Core Memory's semantic retrieval takes priority over OpenClaw's built-in
`memory-core` search.

To opt out (keep OpenClaw's default search while still using Core Memory write/flush paths), set
`enableMemorySearch: false` in the plugin config.

### Streaming message fallback

Some OpenClaw streaming channels can deliver replies without dispatching the typed `agent_end` hook.
The bridge keeps `agent_end` as the primary write path, but also listens to `message_received` and
`message_sent` by default. When an inbound user message is followed by an outbound delivered message
and no `agent_end` appears for that session/run after a short delay, the bridge sends the paired
user/assistant text through the same `core_memory.integrations.openclaw.agent_end_bridge` Python
entrypoint.

To opt out, set `enableMessageTurnFallback: false`. To tune the duplicate-suppression delay, set
`messageTurnFallbackDelayMs` or `CORE_MEMORY_MESSAGE_TURN_FALLBACK_DELAY_MS`.

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

For OpenClaw v2026.6.8 and newer, the installer keeps
`plugins.entries.core-memory-bridge` and sets `hooks.allowConversationAccess: true`.
See `openclaw-v2026.6.8-install.md` for the required config shape.

## What onboarding does
- Installs plugin from `plugins/openclaw-core-memory-bridge`
- Enables plugin id `core-memory-bridge`
- Ensures `plugins.entries.core-memory-bridge.hooks.allowConversationAccess=true`
- Stores `pythonBin`, `coreMemoryRoot`, and `coreMemoryRepo` in the plugin entry config
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
  - `core_memory.integrations.openclaw.agent_end_bridge`
  - `core_memory.integrations.openclaw.read_bridge`
  - `core_memory.integrations.openclaw.compaction_queue`
- The plugin loads prompt/skill instructions from `coreMemoryRepo/docs/integrations/openclaw/`.
- `CORE_MEMORY_ROOT` controls where Core Memory durable artifacts are written.
- `CORE_MEMORY_ENABLED=0` cleanly no-ops bridge turn ingest/flush paths (safe rollback switch).
