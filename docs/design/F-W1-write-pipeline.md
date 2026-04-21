# F-W1: Move Non-Critical Write Stages to Side Effect Queue

**Status:** Approved — ready for implementation.
**Fix ID:** F-W1 (P0)
**Author:** Christopher Dedow / Claude Code
**Date:** 2026-04-20

---

## Problem

`process_turn_finalized_impl` runs 14 stages synchronously on the critical path, including an inline LLM call (`invoke_turn_crawler_agent`), association mining, claim extraction, decision passes, and memory-outcome classification. This adds real latency per turn and bakes in single-writer assumptions.

Current p95 per-turn latency is dominated by the LLM call in `invoke_turn_crawler_agent` — everything after `process_memory_event` is post-commit enrichment that doesn't affect the turn's success.

## Proposed Critical Path

The critical path should be: **normalize → emit event → persist bead → return**.

### Stages staying on critical path

| # | Stage | Rationale |
|---|-------|-----------|
| 1 | `normalize_turn_request` → `mark_turn_checkpoint` | Idempotency + input validation |
| 2 | `maybe_emit_finalize_memory_event` | Append-only JSONL (write authority) |
| 3 | Agent-authored gate check (F-W2) | Structural coverage validation |
| 4 | `try_claim_memory_pass` | Optimistic lock — single writer per turn |
| 5 | `process_memory_event` | Persist bead and links |

### Stages moving to side effect queue

| # | Stage | Current location in pipeline |
|---|-------|------------------------------|
| 1 | `build_crawler_context` + `invoke_turn_crawler_agent` | Pre-persist (LLM call) |
| 2 | `resolve_reviewed_updates` | Post-crawler |
| 3 | `run_association_pass` | Post-persist |
| 4 | `extract_and_attach_claims_fn` | Post-persist |
| 5 | `queue_preview_associations` | Post-persist |
| 6 | `merge_crawler_updates` | Post-persist |
| 7 | `run_session_decision_pass` | Post-persist |
| 8 | `emit_claim_updates_fn` | Post-persist |
| 9 | `classify_memory_outcome_fn` → `write_memory_outcome_to_bead_fn` | Post-persist |
| 10 | `emit_agent_turn_quality_metric` | Observability (non-critical) |

## Queue Design

### Leveraging the existing `side_effect_queue` module

`core_memory/runtime/side_effect_queue.py` already implements:
- JSON-based queue in `.beads/events/side-effects-queue.json`
- Lease-based claim with `_CLAIM_LEASE_SECONDS = 120`
- `store_lock` protection
- Existing kinds: `dreamer-run`, `neo4j-sync`, `health-recompute`

**Proposed approach:** Add a new kind `turn-enrichment` that carries the turn context needed for post-persist stages.

### Queue entry shape

```python
{
    "kind": "turn-enrichment",
    "turn_id": str,
    "session_id": str,
    "bead_id": str,         # from process_memory_event delta
    "root": str,
    "enqueued_at": str,     # ISO timestamp
    "attempt": 0,
    "stages": [
        "crawler",          # build_crawler_context + invoke + resolve
        "association",      # run_association_pass
        "claims",           # extract_and_attach_claims_fn
        "preview_assoc",    # queue_preview_associations
        "decision_pass",    # run_session_decision_pass
        "claim_updates",    # emit_claim_updates_fn
        "memory_outcome",   # classify + write_memory_outcome
        "quality_metric",   # emit_agent_turn_quality_metric
    ],
    "completed_stages": [],
}
```

### Durability

The repo already ships a `SqliteBackend` (`core_memory/persistence/backend.py`) with WAL mode, indexed tables, and atomic writes via `memory.db`. The enrichment queue should use a table in this existing database rather than the current JSON file queue. This gives crash recovery, atomic enqueue/dequeue, and WAL-mode concurrency out of the box.

Proposed table:

```sql
CREATE TABLE IF NOT EXISTS enrichment_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    bead_id     TEXT NOT NULL,
    enqueued_at TEXT NOT NULL,
    attempt     INTEGER DEFAULT 0,
    stages      TEXT NOT NULL,           -- JSON array of stage names
    completed   TEXT NOT NULL DEFAULT '[]', -- JSON array of completed stage names
    status      TEXT NOT NULL DEFAULT 'pending'  -- pending | processing | done | failed
);
```

If the SQLite backend is not active (user is on `JsonFileBackend`), fall back to the existing JSON file queue in `.beads/events/side-effects-queue.json`.

### Execution model

- **Synchronous drain after return:** After `process_turn_finalized_impl` returns, the caller (engine.py) drains the queue synchronously. This preserves the current single-threaded model while cutting the critical path.
- **Async drain (future):** A background worker polls the queue. Out of scope for DCMEX.

## Failure Modes

### What happens if a queued stage fails?

| Failure | Behavior | Recovery |
|---------|----------|----------|
| Crawler LLM call fails | Stage marked failed, remaining stages still run | Retry on next `run_side_effects` call |
| Association pass fails | Bead exists without enrichment | Manual `run_association_pass` or next flush |
| Claim extraction fails | Bead exists without claims | Next turn's claim pass may pick it up |
| Queue file corrupted | Side effects skipped, turn already persisted | Rebuild queue from event log |

**Key invariant:** A queued-stage failure never fails the turn. The bead is already persisted at the point any queued stage runs.

### At-least-once semantics

Each stage is tracked in `completed_stages`. On retry, already-completed stages are skipped. Stages must be **idempotent** — running twice produces the same result.

Stages that are already idempotent today:
- `run_association_pass` — deduplicates by source/target pair
- `queue_preview_associations` — deduplicates by association key
- `emit_agent_turn_quality_metric` — append-only log

Stages that need idempotency review before implementation:
- `invoke_turn_crawler_agent` — LLM call; may produce different results on retry. Accept this as intentional (LLM is nondeterministic).
- `extract_and_attach_claims_fn` — needs dedup on claim subject-slot
- `classify_memory_outcome_fn` — writes to bead; needs check-before-write

## Interaction with Agent-Authored Gate (F-W2)

The gate check runs **on the critical path** (before persist), not in the queue. In `warn` mode, the bead is flagged `structural_coverage_missing=true` and persisted. The queued crawler stage will later enrich the bead — if it succeeds, the flag could be cleared. If it fails, the flag stays as a quality signal.

In `hard` mode, the gate blocks before persist — no bead is created, so nothing enters the queue.

In `off` mode, no gate check runs and the bead is persisted directly.

## Rollback Strategy

If the enrichment queue causes unexpected issues after deployment:

1. **Disable the queue:** Set `CORE_MEMORY_ENRICHMENT_QUEUE=off` (env var, not yet implemented) to run all stages synchronously on the critical path, restoring pre-F-W1 behavior.
2. **Clear the queue:** Delete `.beads/events/side-effects-queue.json` — all pending enrichments are lost, but beads are already persisted.
3. **Revert the PR:** Since beads are persisted on the critical path, reverting enrichment to synchronous loses no data.

## Implementation Sequence

1. Add `turn-enrichment` kind to `side_effect_queue.py`
2. Add `enqueue_turn_enrichment(root, turn_id, session_id, bead_id)` function
3. Add `drain_turn_enrichment(root)` function that runs queued stages
4. Refactor `process_turn_finalized_impl`: critical path ends after `process_memory_event`, enqueue enrichment
5. Call `drain_turn_enrichment` from `engine.py` after `process_turn_finalized_impl` returns
6. Add `CORE_MEMORY_ENRICHMENT_QUEUE` env var (default `on`, `off` to disable)
7. Update tests to verify critical path latency and enrichment completion

## Metrics

After implementation, measure:
- **p95 critical path latency** (target: <100ms without embedding API call)
- **Enrichment completion rate** (target: >99% on first attempt)
- **Enrichment queue depth** (should stay near 0 in steady state)

## Open Questions

None blocking. If questions arise during implementation, raise as GitHub issues tagged `pre-dcmex-question`.
