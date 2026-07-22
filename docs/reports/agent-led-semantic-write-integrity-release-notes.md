# Agent-Led Semantic Write Integrity — Release Notes

**Status:** Slices 1–7 implemented; hosted copied/live backfill run pending

**Release:** `1.1.1`

## Compatibility schedule

`1.1.1` is the single published compatibility window for the narrow rollout
aliases below. The following cutovers are assigned to `1.2.0`, after their
ledger gates pass:

- reject `core-memory graph backfill-causal-links --apply` with migration
  guidance;
- remove the read-only `/v1/memory/hygiene/seed-backfill` route and its retired
  implementation module;
- stop accepting `metadata.crawler_updates` and `bead_judge=llm` as authoring
  aliases;
- stop reading association candidate/judge v1 records after the persisted-store
  audit and migration/review gate are complete.

The event-only `emit_turn_finalized(...)` facade and explicit
`warn`/`off` plus legacy `enforce`/`observe` mode names have broader integration
impact. They remain until `2.0.0`; callers must migrate to
`write_turn_finalized(...)` and `hard` mode before that major release.

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
- New association candidate records use `memory.association_candidates.v2` and
  contain relationship-neutral bead pairs plus shortlist signals. The
  association judge uses `memory.association_judge.v2` and must author the
  relationship, direction, evidence, confidence, reason, and truth basis—or
  return `no_link`. Stored v1 candidates remain readable for one compatibility
  window for inspection, but v1 proposed fields are diagnostic only and are
  never copied into a new decision.
- Association health and doctor output report judge readiness, pending-judge
  count and age, and five-minute warning / sixty-minute critical thresholds.
  Graph health reports structural continuity separately from semantic causal
  relationships.
- Governed `reauthor_memory` and `retry_pending_semantic` maintenance actions
  are dry-run-first, require operator authority and apply idempotency, and reuse
  the complete delegated `turn_memory_authoring` contract. Reauthoring appends
  derived or explicit revision beads while preserving the exact source bead;
  pending retries commit through the canonical finalized-turn path.
- Live semantic maintenance requires a successful copied-tenant apply receipt
  for the exact same plan. Hosted stores bind their declared role through
  `CORE_MEMORY_MAINTENANCE_ENVIRONMENT`, preventing a live root from being
  submitted as a copied tenant. Receipts and the append-only maintenance audit record
  sources examined, authorship/task provenance, contract version, timestamps,
  primary/derived writes, validation failures, pending age, and post-commit
  association coverage.
- `semantic_backfill_report` keeps legacy pre-contract, v1-authored, and
  governed-backfill cohorts separate for retrieval framing, claims, semantic
  keys, relationships, and causal edges.
- The temporary `/v1/memory/hygiene/seed-backfill` route is now read-only.
  `apply=true` returns `seed_quality_backfill_apply_retired` and points callers
  to the copied-tenant-first `reauthor_memory` workflow; direct bead rewrites
  and automatic Dreamer acceptance are no longer available.

These version assignments are normative. A removal may move later if its ledger
gate is incomplete, but it must not ship earlier than the assigned version.
