# OpenClaw Agent-End Bridge (Thin Extract/Dedupe/Emit)

Status: Supporting

This bridge implements the thin integration shape:

`agent_end` -> extract finalized turn payload -> dedupe -> `emit_turn_finalized(...)` -> return

## Why

- Avoids putting memory-engine logic inside OpenClaw hook runtime.
- Uses full-turn content from `agent_end` instead of transport-chunked payloads.
- Preserves Core Memory as authority for write-side processing.

## Implementation

- Module: `core_memory.integrations.openclaw_agent_end_bridge`
- Entry: `process_agent_end_event(event, ctx, root=None)`
- CLI mode: reads JSON from stdin and prints JSON result

## Input (stdin JSON)

```json
{
  "event": {
    "messages": [{"role":"user","content":"..."},{"role":"assistant","content":"..."}],
    "success": true,
    "error": null,
    "durationMs": 1234,
    "runId": "run-123"
  },
  "ctx": {
    "sessionId": "session-1",
    "sessionKey": "main",
    "agentId": "main",
    "trigger": "user"
  },
  "root": "/home/node/.openclaw/workspace/memory"
}
```

## Output

```json
{"ok": true, "emitted": true, "event_id": "...", "session_id": "...", "turn_id": "..."}
```

or dedupe/skip result:

```json
{"ok": true, "emitted": false, "reason": "deduped"}
```

## Dedupe state

Persisted at:

- `<CORE_MEMORY_ROOT>/.beads/events/agent-end-bridge-state.json`

Key format:

- `session_id:turn_id`

## Example shell wiring

```bash
python -m core_memory.integrations.openclaw_agent_end_bridge <<'JSON'
{"event":{"messages":[{"role":"user","content":"u"},{"role":"assistant","content":"a"}]},"ctx":{"sessionId":"s1","sessionKey":"main"}}
JSON
```

## Notes

- Recursion guard skips `trigger=memory` and `metadata.origin=MEMORY_PASS`.
- Fail-open behavior: bridge returns without raising into caller runtime.
