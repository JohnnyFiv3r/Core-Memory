# MCP Typed Read Contract

Status: canonical adapter contract (MCP-1)

Purpose: expose practical typed read tools for MCP clients while preserving one retrieval authority path.

## Tools

- `query_current_state`
- `query_temporal_window`
- `query_causal_chain`
- `query_contradictions`

All tools map to existing canonical retrieval/runtime surfaces:

- `core_memory.retrieval.tools.memory.execute(...)`
- `core_memory.retrieval.tools.memory.trace(...)`
- `core_memory.claim.resolver.resolve_all_current_state(...)`

No MCP read tool introduces a second retrieval personality.

## HTTP Adapter Endpoints

- `POST /v1/mcp/query-current-state`
- `POST /v1/mcp/query-temporal-window`
- `POST /v1/mcp/query-causal-chain`
- `POST /v1/mcp/query-contradictions`

## Output Contracts

Each endpoint returns:

- `ok: bool`
- `contract: mcp.query_<tool>.v1`
- `query: {...typed request echo...}`
- canonical payload section (`retrieval` or `trace`) with canonical result shapes

## Guardrails

- read-only: no canonical write mutation
- retrieval semantics remain canonical (`search`, `trace`, `execute`)
- contradiction/current-state views remain claim-layer-derived
