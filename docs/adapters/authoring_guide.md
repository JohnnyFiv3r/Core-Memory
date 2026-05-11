# Adapter authoring guide

This guide shows how to wire any host runtime into Core Memory through the three-hook adapter lifecycle contract. Most custom adapters should take less than an hour: map host events, generate stable ids, pass turn data, and surface errors.

Read first: [`contract.md`](contract.md).

## Mental model

Your adapter owns host-system translation. Core Memory owns memory semantics.

The adapter detects:

1. a session started → call `on_session_start` / `process_session_start`
2. a turn finished → call `on_turn_end` / `process_turn_finalized`
3. compaction should run → call `on_session_end` / `process_flush`

Do not write `.beads`, `.turns`, archive files, semantic indexes, or rolling-window files directly.

## Minimal worked example

```python
from __future__ import annotations

import uuid
from core_memory.runtime.engine import (
    process_session_start,
    process_turn_finalized,
    process_flush,
)

class MyRuntimeAdapter:
    def __init__(self, *, root: str = ".", session_id: str | None = None):
        self.root = root
        self.session_id = session_id or f"my-runtime-{uuid.uuid4().hex[:8]}"
        self._turn_counter = 0

    def on_session_start(self) -> dict:
        return process_session_start(
            root=self.root,
            session_id=self.session_id,
            source="my_runtime",
        )

    def on_turn_end(self, *, user_text: str, assistant_text: str, tools: list[dict] | None = None) -> dict:
        self._turn_counter += 1
        turn_id = f"turn-{self._turn_counter}-{uuid.uuid4().hex[:8]}"
        return process_turn_finalized(
            root=self.root,
            session_id=self.session_id,
            turn_id=turn_id,
            transaction_id=f"tx-{turn_id}",
            trace_id=f"tr-{turn_id}",
            user_query=user_text,
            assistant_final=assistant_text,
            origin="USER_TURN",
            tools_trace=tools or [],
            metadata={"framework": "my_runtime", "source": "adapter"},
        )

    def on_session_end(self) -> dict:
        return process_flush(
            root=self.root,
            session_id=self.session_id,
            promote=True,
            token_budget=800,
            max_beads=10,
            source="my_runtime_session_end",
        )
```

Line-by-line responsibilities:

- `session_id`: stable session grouping key. Use the host runtime id when available.
- `turn_id`: unique within the session. Never reuse it.
- `transaction_id`/`trace_id`: caller-side observability. Generate if the host lacks them.
- `metadata`: free-form adapter context; populate high-value keys when available.
- `tools_trace`: pass host tool calls if exposed.
- `process_flush`: may run at actual session end, threshold, idle timer, or scheduled interval.

## Hook timing reference

- `on_session_start`: before the first turn for a session. Duplicate calls are safe.
- `on_turn_end`: after the assistant response is complete. Before-send is safest; after-send is acceptable if that is the only host hook.
- `on_session_end`: one or more times per session. This is the only hook that can be scheduled.

## Common pitfalls

1. **Skipping session start.** The turn may still write, but rolling-window continuity and diagnostics lose a clean start marker.
2. **Reusing `turn_id`.** Undefined behavior. Generate stable unique ids per session.
3. **Concurrent turn writes for one session.** Serialize adapter-side. Runtime locking is only a safety net.
4. **Swallowing runtime errors.** Fail open if your host must keep serving, but log/surface the Core Memory error.
5. **Bypassing the runtime.** Direct `MemoryStore.add_bead()` calls skip the canonical write path, field judging, claims, associations, and side effects.
6. **Skipping flush forever.** You lose compaction, promotion review, and long-session hygiene.
7. **Overfilling semantic metadata.** Pass what the host knows; do not invent `crawler_updates`, claims, or associations in the adapter.

## Verify-your-adapter checklist

- [ ] Adapter calls `on_session_start` for every new session.
- [ ] Every `on_turn_end` is preceded by `on_session_start` for the same `session_id`.
- [ ] `turn_id` values are unique within the session.
- [ ] Legacy `user_query` / `assistant_final` are non-empty when the host has both sides.
- [ ] Post-PRD #1 `Turn.speaker` values are non-empty when using the `Turn` shape.
- [ ] `metadata.retrieved_beads` is populated when the host retrieved Core Memory context.
- [ ] `tools_trace` is populated when the host exposes tool events.
- [ ] `on_session_end` runs at least once per session or from a scheduler.
- [ ] Same-session `on_turn_end` calls are serialized adapter-side.
- [ ] Runtime errors are visible to operators.
- [ ] Adapter persistence and retrieval use canonical Core Memory surfaces only.

## Reference implementations

- [OpenClaw adapter](openclaw.md)
- [PydanticAI adapter](pydanticai.md)
- [LangChain adapter](langchain.md)
- [MCP typed-write adapter](mcp.md)
