# Write-side Flow

Status: Canonical

## Per-turn (primary)
1. Runtime finalizes top-level assistant reply.
2. Adapter emits canonical finalized-turn payload (`session_id`, `turn_id`, user/assistant text, metadata).
3. `emit_turn_finalized(...)` helper records canonical event envelope.
4. `memory_engine.process_turn_finalized(...)` is the canonical idempotent write boundary per `session_id:turn_id`.

## In-session semantic judgment updates
- Agent-reviewed crawler outputs are canonical for:
  - bead creation judgment
  - promotion decisions
  - association decisions
- Outputs queue to session-local side logs.
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
- Deterministic worker outputs are preview-only; canonical semantic writes are agent-reviewed
