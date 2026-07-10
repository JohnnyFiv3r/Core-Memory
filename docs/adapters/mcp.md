# MCP adapter surfaces

Core Memory now exposes two MCP-adjacent surfaces:

- The streamable-HTTP MCP protocol server at `/mcp`, including the
  `core-memory.agent-guide` prompt for clients that surface MCP prompts.
- The older typed REST operations under `/v1/mcp/*`, including the
  turn-finalized boundary as `write_turn_finalized`.

Both surfaces are active. The protocol server is the normal setup path for
MCP-capable clients; the typed REST endpoints remain a compatibility and direct
integration surface.

Implementation references:

- `core_memory/integrations/mcp/protocol_server.py`
- `core_memory/integrations/mcp/registry.py`
- `core_memory/integrations/mcp/tools/`
- `core_memory/integrations/mcp/typed_write.py`
- `core_memory/integrations/mcp/typed_read.py`
- HTTP wrappers in `core_memory/integrations/http/server.py`
- Contract docs: `docs/contracts/mcp_typed_write_contract.md` and `docs/contracts/mcp_typed_read_contract.md`
- Setup guide: `docs/integrations/mcp/quickstart.md`

## Setup

For stock MCP clients, use `core-memory mcp install` or the manual
configuration in `docs/integrations/mcp/quickstart.md`.

For direct adapters that do not speak the MCP protocol, use the typed
write/read functions directly or through the HTTP server endpoints that wrap
them.

## Hook mapping

| MCP tool/endpoint | Canonical hook | Runtime function | Notes |
|---|---|---|---|
| `write_turn_finalized` | `on_turn_end` | `process_turn_finalized` | Canonical typed write boundary for completed turns; its schema includes the complete authored-update contract. |
| `capture` | `on_turn_end` convenience | `Memory.capture` → `process_turn_finalized` | Declares the same `crawler_updates` and `authoring_mode` fields despite `additionalProperties: false`. |
| HTTP `/v1/memory/session-start` | `on_session_start` | `process_session_start` | Present in HTTP server, not currently listed as an MCP typed-write tool. |
| HTTP `/v1/memory/session-flush` | `on_session_end` | `process_flush` | Present in HTTP server, not currently listed as an MCP typed-write tool. |
| `apply_reviewed_proposal` | review/adjudication | Dreamer candidate path | Not one of the three adapter lifecycle hooks. |
| `submit_entity_merge_proposal` | review/adjudication | Dreamer candidate path | Not one of the three adapter lifecycle hooks. |

## Configuration

`write_turn_finalized` fields:

- Required: `root`, `session_id`, `turn_id`, `turns`.
- Optional: `transaction_id`, `trace_id`, `metadata`, `tools_trace`, `mesh_trace`, `window_turn_ids`, `window_bead_ids`, `origin`, typed `crawler_updates`, and `authoring_mode=inline|delegated`.

The machine-readable schema for `crawler_updates` is generated from
`AgentAuthoredUpdatesV1`; MCP agents therefore discover the same required bead,
association, claim, key, and type-specific fields as Python and HTTP callers.

The typed tool generates transaction/trace ids when omitted and returns the
same `memory.turn_finalized_receipt.v2` as processed Python and HTTP callers.
The receipt separates canonical bead commitment, derived-write failures,
association coverage, and durable queue state.

## Verification

Call `write_turn_finalized(...)` with a unique `session_id` and `turn_id`, then verify:

- result has `ok: true` or exposes the runtime error,
- result contract is `memory.turn_finalized_receipt.v2`,
- `semantic_status` is `committed` only when canonical current-turn bead lookup
  succeeds,
- a turn/bead appears in the configured Core Memory root.

For HTTP lifecycle endpoints, smoke:

- `POST /v1/memory/session-start`
- `POST /v1/memory/turn-finalized`
- `POST /v1/memory/session-flush`

## Common pitfalls

1. **Assuming full three-hook MCP lifecycle exists as typed tools.** Today only turn-finalized is exposed in `typed_write.py`; session start/flush exist through HTTP server routes.
2. **Missing `turns`.** The typed tool rejects missing required attributed turn list.
3. **Treating proposal tools as lifecycle hooks.** Proposal tools are review workflows, not session/turn lifecycle events.
4. **Skipping session start/flush.** If using only `write_turn_finalized`, you may miss explicit continuity open and compaction boundaries.
5. **Confusing `/mcp` with `/v1/mcp/*`.** `/mcp` is the protocol transport for
   MCP clients. `/v1/mcp/*` remains the typed REST compatibility surface.

## Audit notes

- Turn-end hook: aligned through `write_turn_finalized`.
- Session-start/session-end: available via HTTP routes, not as MCP typed-write tools today.
- Full MCP protocol server: shipped at `/mcp`.
- Agent guide prompt: shipped as `core-memory.agent-guide`.
