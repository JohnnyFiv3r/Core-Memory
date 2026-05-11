# MCP typed-write adapter

The current MCP integration exposes typed Core Memory operations. For lifecycle writes, it currently exposes the turn-finalized boundary as `write_turn_finalized`.

Implementation references:

- `core_memory/integrations/mcp/typed_write.py`
- `core_memory/integrations/mcp/typed_read.py`
- HTTP wrappers in `core_memory/integrations/http/server.py`
- Contract docs: `docs/contracts/mcp_typed_write_contract.md` and `docs/contracts/mcp_typed_read_contract.md`

## Setup

Use the typed write/read functions directly or through the HTTP server endpoints that wrap them. A full MCP streamable-HTTP server endpoint is tracked separately; this page documents the existing typed MCP surface.

## Hook mapping

| MCP tool/endpoint | Canonical hook | Runtime function | Notes |
|---|---|---|---|
| `write_turn_finalized` | `on_turn_end` | `process_turn_finalized` | Canonical typed write boundary for completed turns. |
| HTTP `/v1/memory/session-start` | `on_session_start` | `process_session_start` | Present in HTTP server, not currently listed as an MCP typed-write tool. |
| HTTP `/v1/memory/session-flush` | `on_session_end` | `process_flush` | Present in HTTP server, not currently listed as an MCP typed-write tool. |
| `apply_reviewed_proposal` | review/adjudication | Dreamer candidate path | Not one of the three adapter lifecycle hooks. |
| `submit_entity_merge_proposal` | review/adjudication | Dreamer candidate path | Not one of the three adapter lifecycle hooks. |

## Configuration

`write_turn_finalized` fields:

- Required: `root`, `session_id`, `turn_id`, `user_query`, `assistant_final`.
- Optional: `transaction_id`, `trace_id`, `metadata`, `tools_trace`, `mesh_trace`, `window_turn_ids`, `window_bead_ids`, `origin`.

The typed tool generates transaction/trace ids when omitted and returns a contract-tagged result with `event_id`, `processed`, and runtime result details.

## Verification

Call `write_turn_finalized(...)` with a unique `session_id` and `turn_id`, then verify:

- result has `ok: true` or exposes the runtime error,
- result contract is `mcp.write_turn_finalized.v1`,
- a turn/bead appears in the configured Core Memory root.

For HTTP lifecycle endpoints, smoke:

- `POST /v1/memory/session-start`
- `POST /v1/memory/turn-finalized`
- `POST /v1/memory/session-flush`

## Common pitfalls

1. **Assuming full three-hook MCP lifecycle exists as typed tools.** Today only turn-finalized is exposed in `typed_write.py`; session start/flush exist through HTTP server routes.
2. **Missing `user_query` or `assistant_final`.** The typed tool rejects missing required text fields.
3. **Treating proposal tools as lifecycle hooks.** Proposal tools are review workflows, not session/turn lifecycle events.
4. **Skipping session start/flush.** If using only `write_turn_finalized`, you may miss explicit continuity open and compaction boundaries.
5. **Future `/mcp` server work.** The streamable-HTTP MCP transport and prompt/resource exposure are separate follow-up work.

## Audit notes

- Turn-end hook: aligned through `write_turn_finalized`.
- Session-start/session-end: available via HTTP routes, not as MCP typed-write tools today.
- Full MCP protocol server remains future work and should register the adapter instructions as a prompt/resource when it ships.
