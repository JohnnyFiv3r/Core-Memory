# OpenClaw adapter

OpenClaw is a production bridge adapter that listens to OpenClaw lifecycle events and routes finalized turns into Core Memory canonical runtime entrypoints.

Implementation references:

- `plugins/openclaw-core-memory-bridge/index.js`
- `core_memory/integrations/openclaw_agent_end_bridge.py`
- `core_memory/integrations/openclaw_runtime.py`
- Deep implementation docs: `docs/integrations/openclaw/`

## Setup

Use the OpenClaw plugin bridge under `plugins/openclaw-core-memory-bridge/` and configure:

- `pythonBin`: Python executable for Core Memory subprocesses.
- `coreMemoryRoot`: Core Memory store root.
- `coreMemoryRepo`: Core Memory repo path used as subprocess working directory.
- `enableAgentEnd`: enables finalized-turn writes.
- `enableMemorySearch`: enables OpenClaw memory search routing.
- `enableCompactionFlush`: enables compaction queue hook wiring.

The plugin also declares the `core-memory` skill and injects `docs/integrations/openclaw/core-memory-skill-instructions.md` as an OpenClaw memory prompt supplement when the host exposes that API.

## Hook mapping

| OpenClaw event | Canonical hook | Runtime function | Notes |
|---|---|---|---|
| Gateway/plugin registration | prompt/context setup | `registerMemoryPromptSupplement` | Loads Core Memory skill instructions into the agent path. |
| `agent_end` | `on_turn_end` | `process_turn_finalized` via `finalize_and_process_turn` | Extracts last user/assistant messages, creates stable ids, dedupes, then writes through canonical runtime. |
| `memory_search` | retrieval surface | `memory.execute` via `openclaw_read_bridge` | Read path, not one of the three write lifecycle hooks. |
| `after_compaction` when enabled | `on_session_end` enqueue | `openclaw_compaction_queue` | Queues flush work; drain happens asynchronously from `agent_end`. |
| Compaction queue drain | `on_session_end` | `process_flush` | Thin Python bridge owns queue mechanics; runtime owns compaction semantics. |

OpenClaw does not currently expose a dedicated `on_session_start` bridge event in this plugin. Session start semantics are covered by runtime write processing and continuity paths where applicable; this is a known adapter-contract divergence to keep visible.

## Configuration

See `plugins/openclaw-core-memory-bridge/openclaw.plugin.json` and `docs/integrations/openclaw/plugin-setup.md`.

Important operational toggles:

- `enableAgentEnd !== false` keeps the turn-write hook active.
- `enableMemorySearch !== false` keeps read routing active.
- `enableCompactionFlush === true` enables compaction enqueue from `after_compaction`.

## Verification

- Check plugin smoke CI for `bridge-smoke`.
- Inspect `/tmp/core-memory-bridge-hook.log` for `agent_end` activity.
- Confirm `.beads/events/memory-events.jsonl` appends after completed OpenClaw turns.
- Confirm no warning like `core-memory-bridge: agent_end emit failed` appears in Gateway logs.

## Common pitfalls

1. **Assuming hook list introspection proves runtime activity.** Prefer log and event-file movement.
2. **Moving semantics into plugin JS.** The bridge must remain thin; Core Memory runtime owns bead authoring and lifecycle semantics.
3. **Missing skill instructions.** Enabled plugin installs should provide the `core-memory` skill and prompt supplement.
4. **Compaction disabled by default.** `enableCompactionFlush` must be explicitly true for the `after_compaction` enqueue path.
5. **Memory-origin recursion.** The Python bridge skips memory-triggered runs to avoid recursive writes.

## Audit notes

- Canonical turn write path: aligned.
- Read path: aligned to canonical read bridge.
- Session-end/flush path: available through queue when enabled.
- Session-start hook: partial/implicit; no dedicated OpenClaw event mapping today.
