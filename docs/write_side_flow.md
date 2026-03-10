# Write-side Flow

Status: Canonical

## Per-turn (primary)
1. Runtime finalizes top-level assistant reply.
2. Adapter emits canonical finalized-turn payload (`session_id`, `turn_id`, user/assistant text, metadata).
3. `emit_turn_finalized(...)` records canonical event envelope.
4. `memory_engine.process_turn_finalized(...)` runs idempotent processing once per `session_id:turn_id`.

## In-session association updates
- Crawler/agent-reviewed updates queue to session-local side logs.
- Side logs are merged at flush-time into projection surfaces.

## Flush boundary
- `memory_engine.process_flush(...)` performs transition work:
  - enrichment barrier validation
  - side-log merge
  - consolidate/archive and rolling-window refresh

## Rules
- One finalized turn => one idempotent memory pass key
- Fail-open on adapter/runtime integration boundary
- No transcript/index-dump primary write path
