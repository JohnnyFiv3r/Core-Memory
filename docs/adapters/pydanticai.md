# PydanticAI adapter

The PydanticAI adapter wraps an agent run and writes the completed turn to Core Memory through the canonical runtime.

Implementation references:

- `core_memory/integrations/pydanticai/run.py`
- `core_memory/integrations/pydanticai/memory_tools.py`
- Deep implementation docs: `docs/integrations/pydanticai/`

## Setup

Install Core Memory with the PydanticAI extra when applicable, then call one of:

- `run_with_memory(agent, user_query, root=..., session_id=...)`
- `run_with_memory_sync(agent, user_query, root=..., session_id=...)`
- `flush_session(...)` / `flush_session_async(...)` at app-defined flush boundaries

## Hook mapping

| PydanticAI action | Canonical hook | Runtime function | Notes |
|---|---|---|---|
| App creates/opens a conversation | `on_session_start` | not explicit today | No dedicated wrapper currently calls `process_session_start`. Adapter authors should add one in host apps if they need explicit session-start semantics. |
| `run_with_memory(...)` completes agent run | `on_turn_end` | `process_turn_finalized` | Extracts assistant output and writes attributed `turns=[...]` through the runtime. |
| `run_with_memory_sync(...)` completes agent run | `on_turn_end` | `process_turn_finalized` | Sync variant of the same contract. |
| `flush_session(...)` / `flush_session_async(...)` | `on_session_end` | `process_flush` | App-defined boundary; may be called on idle timeout, threshold, shutdown, or explicit session end. |

## Configuration

Key call arguments:

- `root`: Core Memory store path.
- `session_id`: session grouping key.
- `turn_id`: optional; generated if omitted.
- `metadata`: merged into adapter metadata.
- `tools_trace`, `mesh_trace`, `window_turn_ids`, `window_bead_ids`: optional runtime context.

Flush defaults currently used by this adapter:

- `promote=True`
- `token_budget=3000`
- `max_beads=80`

These are higher than the lightweight contract conventions and should be treated as the PydanticAI adapter's measured/production defaults.

## Verification

A minimal smoke is:

1. Run a PydanticAI agent through `run_with_memory_sync(...)` or `run_with_memory(...)`.
2. Check the return value from the agent is unchanged.
3. Inspect the Core Memory store for a new turn/bead under the configured `session_id`.
4. Call `flush_session(...)` and verify it returns an `ok` result.

## Common pitfalls

1. **No explicit session start.** Current helper APIs do not call `process_session_start`; add one in host code if rolling-window injection needs a clean open event.
2. **Fail-open write errors.** The adapter logs debug exceptions and returns the agent result; operators need logging enabled to see write failures.
3. **Missing tool traces.** PydanticAI tools are not automatically harvested unless the caller passes `tools_trace`.
4. **Confusing tools with memory tools.** `memory_tools.py` exposes read/write tools; `run.py` is the lifecycle wrapper.
5. **Flush not automatic.** Apps must decide when `flush_session` fires.

## Audit notes

- Turn-end hook: aligned.
- Session-end hook: aligned.
- Session-start hook: missing explicit helper; document as a known divergence rather than changing runtime signatures in this PRD.
