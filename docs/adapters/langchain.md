# LangChain adapter

The LangChain adapter implements `BaseMemory` and maps LangChain memory methods to Core Memory lifecycle hooks.

Implementation references:

- `core_memory/integrations/langchain/memory.py`
- `core_memory/integrations/langchain/retriever.py`
- Deep implementation docs: `docs/integrations/langchain/`

## Setup

Install Core Memory with LangChain dependencies, then configure `CoreMemory` as the chain memory object:

```python
from core_memory.integrations.langchain.memory import CoreMemory

memory = CoreMemory(root=".", session_id="my-session")
```

## Hook mapping

| LangChain method/event | Canonical hook | Runtime function | Notes |
|---|---|---|---|
| `load_memory_variables(...)` | `on_session_start` | `process_session_start` | Ensures session continuity context before prompt assembly. |
| `save_context(inputs, outputs)` | `on_turn_end` | `process_turn_finalized` | Extracts user/assistant text from configured input/output keys and writes a finalized turn. |
| `clear()` | `on_session_end` | `process_flush` | Treats LangChain clear as a session flush boundary. |

## Configuration

`CoreMemory` fields:

- `root`: Core Memory store path.
- `session_id`: session grouping key.
- `memory_key`: key returned from `load_memory_variables`.
- `input_key`: input dict key for user text.
- `output_key`: output dict key for assistant text.
- `max_items`: rolling-window continuity records to inject.
- `return_messages`: currently false/string-oriented.

Flush defaults used by `clear()`:

- `promote=True`
- `token_budget=1200`
- `max_beads=12`
- `source="langchain_clear"`

## Verification

1. Instantiate `CoreMemory(root=..., session_id=...)`.
2. Call `load_memory_variables(...)`; it should return `{memory_key: str}` and not raise.
3. Run a chain that calls `save_context(...)` or call it directly with input/output text.
4. Verify a turn/bead exists under the configured session.
5. Call `clear()` and verify flush completes without error.

## Common pitfalls

1. **Wrong input/output keys.** If `input_key` or `output_key` do not match your chain, `save_context` may write empty text or skip the turn.
2. **Assuming `clear()` deletes memory.** In this adapter it maps to flush/compaction, not destructive deletion.
3. **No tool trace integration.** LangChain tool events are not currently passed through this memory adapter.
4. **Repeated session start refreshes.** `load_memory_variables` may call `process_session_start` repeatedly; this is safe and refreshes continuity.
5. **String-only return shape.** `return_messages` is not a message-object path today.

## Audit notes

- Session-start hook: aligned through `load_memory_variables`.
- Turn-end hook: aligned through `save_context`.
- Session-end hook: aligned through `clear`.
- Tool trace and richer metadata are not currently harvested.
