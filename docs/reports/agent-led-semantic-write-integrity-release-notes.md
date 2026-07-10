# Agent-Led Semantic Write Integrity — Planned Release Notes

**Status:** Planned; implementation has not shipped

The rollout defined by
`docs/PRD/agent-led-semantic-write-integrity.md` changes the canonical semantic
write path from deterministic-fallback-first behavior to typed inline or
delegated agent authorship.

## Planned compatibility notices

- `metadata.crawler_updates` remains readable for one deprecation window after
  typed top-level `crawler_updates` ships.
- `bead_judge=llm` becomes a compatibility alias for full-schema delegated
  `turn_memory_authoring` for one deprecation window.
- Adding `crawler_updates` and `authoring_mode` to the turn-envelope hash may
  produce superseded-envelope diagnostics during upgraded retries. Memory-pass
  identity remains `(session_id, turn_id)`.
- `core-memory graph backfill-causal-links --apply` is deprecated because it
  applies deterministic semantic relationships. The first rollout release
  keeps the flag with a warning and telemetry while shipping the
  candidate-plus-agent-judge replacement. The following documented release
  rejects `--apply` with migration guidance.

These notices are prospective. Shipped version numbers and exact removal dates
must be filled in by the implementation PR that activates each behavior.
