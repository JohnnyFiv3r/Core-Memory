# V2 Flush Transaction Spec

Status: Canonical (V2-P1)
Purpose: specify staged, idempotent flush protocol from session surface to archive + rolling window.

## Trigger
- Authoritative trigger: memory flush hook.
- Admin fail-safe: manual CLI flush trigger.

## Precondition
- Ensure per-turn enrichment has processed final turn for session (promotion, semantic tags, causal links).

## Stages

### Stage 1 — Enrichment barrier
- Verify latest session turn has completed enrichment pipeline.
- Record stage checkpoint `enrichment_ready`.

### Stage 2 — Archive persistence
- Persist full-fidelity session beads to archive store.
- Preserve bead IDs.
- Record stage checkpoint `archive_persisted`.

### Stage 3 — Rolling projection
- Build rolling window projection from session surface.
- Apply compression only to non-promoted beads in rolling copy.
- Enforce strict recency FIFO token budget (~10k).
- Record stage checkpoint `rolling_written`.

### Stage 4 — Commit checkpoint
- Mark flush success checkpoint with transaction metadata.
- Session flush transaction status becomes `committed`.

## Failure and retry semantics
- On failure, restart from last durable checkpoint stage.
- Replays must be idempotent (no duplicate archive entries, no inconsistent rolling window state).
- Partial stage completion must not produce committed state.

## Required transaction metadata
- session_id
- flush_tx_id
- stage
- stage_timestamp
- envelope/hash marker for idempotency
- commit_status (`pending|committed|failed`)

## Safety requirements
- Deterministic stage ordering.
- Duplicate trigger immunity.
- Crash-safe restart behavior.
