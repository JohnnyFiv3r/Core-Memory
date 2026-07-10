# Agent-Led Semantic Write Integrity — Planned Release Notes

**Status:** Slices 1–3 shipped; hard-authorship cutover proposed

The rollout defined by
`docs/PRD/agent-led-semantic-write-integrity.md` changes the canonical semantic
write path from deterministic-fallback-first behavior to typed inline or
delegated agent authorship.

## Planned compatibility notices

- `CORE_MEMORY_AGENT_AUTHORED_MODE` now defaults to `hard`. `warn` and `off`
  remain explicit compatibility choices; legacy `enforce` and `observe` aliases
  continue to map to `hard` and `off`.
- Missing or invalid hard-mode authorship no longer creates a deterministic
  context stub. The raw turn and pending-semantic state remain durable, and the
  v2 receipt reports `pending` or `repair_required`.
- Full-contract repair is opt-in through
  `CORE_MEMORY_AGENT_AUTHORED_REPAIR=1` or runtime policy. Repair receipts keep
  primary/repair authorship separate and identify every repaired field.

- `metadata.crawler_updates` remains readable for one deprecation window after
  typed top-level `crawler_updates` ships.
- `bead_judge=llm` becomes a compatibility alias for full-schema delegated
  `turn_memory_authoring` for one deprecation window.
- Adding `crawler_updates` and `authoring_mode` to the turn-envelope hash may
  produce superseded-envelope diagnostics during upgraded retries. Memory-pass
  identity remains `(session_id, turn_id)`.
- Processed Python, HTTP, and MCP turn writes converge on
  `memory.turn_finalized_receipt.v2`. The Python
  `emit_turn_finalized(...)` facade remains event-only for one compatibility
  window; new processed Python callers should use `write_turn_finalized(...)`.
- `core-memory graph backfill-causal-links --apply` is deprecated because it
  formerly applied deterministic semantic relationships. It is candidate-only
  immediately: during one compatibility window the flag emits a warning and
  telemetry but writes no causal links. Use `core-memory graph causal-candidates`
  followed by the agent judge/apply flow; a following documented release rejects
  `--apply` with migration guidance.

These notices are prospective. Shipped version numbers and exact removal dates
must be filled in by the implementation PR that activates each behavior.
