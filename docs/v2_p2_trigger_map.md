# V2-P2 Canonical Trigger Map

Status: Draft for implementation
Related: `docs/v2_execution_plan.md`, `docs/v2_invariants.md`

## Goal
Define the canonical in-process trigger set and enforcement semantics for V2-P2.

## Authority model
- Canonical authority: in-process trigger enforcement path.
- Legacy compatibility: sidecar/poller paths during migration only.

## Trigger set

### T-PER-TURN-FINALIZED
Event:
- A top-level user turn finalized event (trace_depth=0, origin != MEMORY_PASS).

Required actions (deterministic order):
1. dedupe/idempotency claim for `(session_id, turn_id, envelope_hash)`
2. per-turn bead write assessment
3. per-turn promotion assessment
4. causal association updates
5. semantic tag updates
6. candidate evaluation refresh
7. mark turn trigger completion checkpoint

### T-FLUSH-START
Event:
- Memory flush hook invocation.

Required actions:
1. enforce final-turn enrichment barrier (T-PER-TURN-FINALIZED complete)
2. begin flush transaction state

### T-FLUSH-ARCHIVE-PERSIST
Required actions:
- persist full-fidelity session beads to archive store (idempotent)

### T-FLUSH-ROLLING-PROJECTION
Required actions:
- build strict recency FIFO rolling projection from session surface
- apply compression to non-promoted rolling copies only

### T-FLUSH-COMMIT
Required actions:
- write commit checkpoint and mark flush transaction committed

### T-ADMIN-FLUSH
Event:
- Manual admin CLI fail-safe flush trigger.

Required actions:
- invoke same canonical flush transaction path as hook-driven flush
- no alternate semantics

## Determinism rules
- trigger execution order is fixed and documented
- tie-breakers for any ranking/selection are explicit and stable
- warning/event diagnostics emitted in stable ordering

## Idempotency keys
- per-turn: `session_id + turn_id + envelope_hash`
- per-flush: `session_id + flush_tx_id`

## Migration note
During P2, sidecar paths remain as compatibility wrappers but cannot remain authority source after P2 completion.
