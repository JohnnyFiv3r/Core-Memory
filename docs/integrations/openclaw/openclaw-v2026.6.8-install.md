# OpenClaw v2026.6.8+ Bridge Install Notes

Status: Canonical runtime note

OpenClaw v2026.6.8 tightened lifecycle hook access. The Core Memory bridge must now be present in
`plugins.entries.core-memory-bridge`, not only in `plugins.allow`, and the entry must grant
conversation access for lifecycle hooks.

## Required Config Shape

`core-memory openclaw onboard` and `scripts/openclaw_bridge_install.sh` patch this shape automatically:

```json
{
  "plugins": {
    "allow": ["core-memory-bridge"],
    "entries": {
      "core-memory-bridge": {
        "enabled": true,
        "hooks": {
          "allowConversationAccess": true
        },
        "config": {
          "pythonBin": "python3",
          "coreMemoryRoot": "/path/to/core-memory-store",
          "coreMemoryRepo": "/path/to/Core-Memory",
          "enableAgentEnd": true,
          "enableMemorySearch": true,
          "enableCompactionFlush": false,
          "enableMessageTurnFallback": true
        }
      }
    }
  }
}
```

Do not remove `plugins.entries.core-memory-bridge` on OpenClaw v2026.6.8+. Older hardening guidance
that removed stale entries is obsolete for this runtime.

## Canonical Modules

The installed plugin must call the consolidated package paths:

- `core_memory.integrations.openclaw.agent_end_bridge`
- `core_memory.integrations.openclaw.read_bridge`
- `core_memory.integrations.openclaw.compaction_queue`

Flat module paths such as `core_memory.integrations.openclaw_agent_end_bridge` are stale and should
fail doctor validation.

## Install And Verify

```bash
export CORE_MEMORY_REPO=/path/to/Core-Memory
export CORE_MEMORY_ROOT=/path/to/core-memory-store

core-memory openclaw onboard
# or:
"$CORE_MEMORY_REPO/scripts/openclaw_bridge_install.sh"

restart-openclaw-gateway
"$CORE_MEMORY_REPO/scripts/openclaw_bridge_doctor.sh"
```

Successful runtime evidence:

- `/tmp/core-memory-bridge-hook.log` has `register coreMemoryRoot=...`.
- Module checks report `ok=true` for the canonical module paths.
- A completed turn logs `agent_end ... ok=true emitted=true`, or streaming channels log
  `message_sent fallback_result ok=true emitted=true`.
- `$CORE_MEMORY_ROOT/.beads/events/memory-events.jsonl` receives the turn.

