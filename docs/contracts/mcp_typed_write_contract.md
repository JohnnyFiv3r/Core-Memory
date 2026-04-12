# MCP Typed Write Contract

Status: canonical adapter contract (MCP-2)

Purpose: expose safe, typed write operations for MCP clients while preserving canonical write authority.

## Tools

- `write_turn_finalized`
- `apply_reviewed_proposal`
- `submit_entity_merge_proposal`

Canonical mappings:

- `write_turn_finalized` -> `core_memory.runtime.engine.process_turn_finalized(...)`
- `apply_reviewed_proposal` -> `core_memory.runtime.dreamer_candidates.decide_dreamer_candidate(...)`
- `submit_entity_merge_proposal` -> `core_memory.runtime.dreamer_candidates.submit_entity_merge_candidate(...)` (review queue only)

## HTTP Adapter Endpoints

- `POST /v1/mcp/write-turn-finalized`
- `POST /v1/mcp/apply-reviewed-proposal`
- `POST /v1/mcp/submit-entity-merge-proposal`

## Authority + Safety Rules

- No MCP write bypasses canonical runtime/store authority.
- Proposal submission is review-queue only and does not directly mutate truth state.
- Reviewed proposal apply remains gated by explicit decision (`accept`/`reject`) and optional canonical apply.
