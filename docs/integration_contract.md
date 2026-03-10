# Integration Contract

Status: Canonical

All adapters must converge on canonical finalized-turn ingestion.

## Required payload (conceptual)
- `session_id`
- `turn_id`
- `user_query` / `user_message`
- `assistant_final`
- timestamps / trace metadata
- optional tool outputs

## Required behavior
- emit finalized turn once per top-level turn
- idempotent dedupe by `session_id:turn_id`
- fail-open at integration boundary
- optional flush/session-end triggers may be supported by runtime

## Adapter classification
- **Native finalized-turn adapter**: emits from runtime commit/finalize event
- **Bridge adapter**: reconstructs finalized turns and feeds canonical ingress
- **Fallback/polling**: compatibility-only, non-primary

## Launch adapter set
- OpenClaw
- SpringAI
- PydanticAI

Additional adapters should be added only when they can satisfy this contract cleanly.
