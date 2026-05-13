# Session enrichment delta Phase 1 analysis

Status: Phase 1 analysis artifact for #9. This document is intentionally documentation-only. It records the current write paths, window surfaces, side effects, and idempotency boundaries before any `session_enrichment_delta.v1` adapter code is introduced.

## Scope and non-goals

This artifact covers the current turn-finalization and post-persist enrichment flow across:

- `/home/node/.openclaw/workspace/Core-Memory/core_memory/runtime/turn_flow.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/runtime/enrichment.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/association/crawler_contract.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/runtime/engine.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/runtime/side_effect_queue.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/claim/turn_integration.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/claim/update_policy.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/persistence/store_claim_ops.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/entity/registry.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/runtime/decision_pass.py`
- `/home/node/.openclaw/workspace/Core-Memory/core_memory/runtime/session_surface.py`

Non-goals for this chunk:

- No runtime behavior changes.
- No delta adapter implementation.
- No movement of semantic policy into OpenClaw/plugin bridge code.
- No full `#5` grounding-hash validation or `#6` benchmark/eval layer.

## Current call graph

### Synchronous turn-finalization path

1. `process_turn_finalized(...)` in `core_memory/runtime/engine.py` delegates into `process_turn_finalized_impl(...)` in `core_memory/runtime/turn_flow.py`.
2. `process_turn_finalized_impl(...)` normalizes the request with `normalize_turn_request(...)`.
3. It preflights the agent-authored crawler update gate before any semantic checkpoint or store write:
   - metadata-provided `crawler_updates`, or
   - `CORE_MEMORY_AGENT_CRAWLER_CALLABLE`, or
   - default agent invocation path.
4. If the gate blocks, the function returns an `error_agent_updates_missing` / `error_agent_semantic_coverage_missing` style response with `authority_path: canonical_in_process` and does not write turn state.
5. On non-blocked requests, `mark_turn_checkpoint(...)` writes the latest turn checkpoint.
6. `maybe_emit_finalize_memory_event(...)` emits a finalized turn memory event unless guarded by trace/origin/idempotency rules.
7. `try_claim_memory_pass(...)` claims the pending memory pass and moves it to `running`.
8. `process_memory_event(...)` writes the canonical turn bead and returns `delta` containing the committed bead id.
9. `build_crawler_context(root, session_id, limit=200)` builds a post-write session context from the session JSONL surface.
10. The crawler agent is invoked with the context, then `_resolve_reviewed_updates(...)` applies the agent-authored gate/fallback rules.
11. `_ensure_turn_creation_update(...)` guarantees one current-turn creation candidate and re-judges semantic bead fields with `judge_bead_fields(...)`.
12. A fresh `build_crawler_context(..., limit=200)` rebuilds context for post-write enrichment.
13. If `CORE_MEMORY_ENRICHMENT_QUEUE` is enabled, `enqueue_turn_enrichment(...)` writes a `turn-enrichment` side-effect event and returns immediately.
14. If the queue is disabled, enrichment runs inline in the old order:
    - `run_association_pass(...)`
    - `extract_and_attach_claims(...)`
    - `_queue_preview_associations(...)`
    - `merge_crawler_updates(...)`
    - `run_session_decision_pass(...)`
    - `emit_claim_updates(...)`
    - memory outcome classification/write
    - agent turn quality metric

### Queued enrichment path

1. `enqueue_turn_enrichment(...)` writes to `.beads/events/side-effects-queue.json` with kind `turn-enrichment` and idempotency key `enrich-{session_id}-{turn_id}`.
2. `drain_side_effect_queue(...)` leases ready side-effect events and calls `process_side_effect_event(...)`.
3. `process_side_effect_event(...)` dispatches kind `turn-enrichment` to `run_turn_enrichment(...)`.
4. `run_turn_enrichment(...)` runs these post-persist stages independently:
   - Stage 1: `run_association_pass(...)`
   - Stage 2: `extract_and_attach_claims(...)`
   - Stage 3: preview association queueing
   - Stage 4: `merge_crawler_updates(...)`
   - Stage 5: `run_session_decision_pass(...)`
   - Stage 6: `emit_claim_updates(...)`
   - Stage 7: memory outcome classification/write
   - Stage 8: quality metric emission
5. A failure in one queued stage is logged in `stages_failed` and does not undo the already-committed turn bead.

## Persistence targets and schemas in play

### Turn event and pass state

- `.beads/events/memory-events.jsonl`
  - Written by `emit_memory_event(...)` through `maybe_emit_finalize_memory_event(...)`.
  - Contains `{event, envelope}` rows.
  - Envelope hashes include turn content and trace fields.
- `.beads/events/memory-pass-state.json`
  - Managed by `mark_memory_pass(...)`, `get_memory_pass(...)`, and `try_claim_memory_pass(...)`.
  - Statuses include `pending`, `running`, `done`, and `failed`.
- `.beads/events/memory-pass-status.jsonl`
  - Append-only status history for pass state transitions.

### Canonical bead surfaces

- `.beads/session-{session_id}.jsonl`
  - Append-only session bead surface read by `read_session_surface(...)`.
  - `build_crawler_context(...)` uses the last `limit` rows from this file.
- `.beads/index.json`
  - Projection containing `beads`, `associations`, stats, entity registry, and promotion state.
  - `add_bead_for_store(...)` writes the canonical bead into the session JSONL and index projection under store lock.
  - `merge_crawler_updates(...)`, `decide_session_promotion_states(...)`, `write_claims_to_bead(...)`, `write_claim_updates_to_bead(...)`, `write_memory_outcome_to_bead(...)`, and entity registry sync also mutate this projection.

### Crawler side logs and quarantine

- `.beads/events/crawler-updates-{session_id}.jsonl`
  - Written by `apply_crawler_updates(...)` for queued promotion marks, association appends, and association lifecycle actions.
  - Rows use `schema: openclaw.memory.crawler_update.v1`.
- `.beads/events/association-quarantine.jsonl`
  - Written when association payload validation/normalization fails.
  - Current quarantine is association-specific and should become the model for invalid delta-row quarantine in `session_enrichment_delta.v1`.

### Side-effect queue

- `.beads/events/side-effects-queue.json`
  - JSON array of side-effect events.
  - `turn-enrichment` payload contains `session_id`, `turn_id`, `bead_id`, `reviewed_updates`, `crawler_visible_bead_ids`, metadata, and `window_bead_ids`.
- `.beads/events/side-effects-queue-state.json`
  - Circuit breaker and retry state.

### Claims and claim updates

- Canonical write target: `.beads/index.json -> beads[bead_id].claims` and `.beads/index.json -> beads[bead_id].claim_updates`.
- `write_claims_to_bead(...)` appends normalized `Claim` rows.
- `write_claim_updates_to_bead(...)` appends normalized `ClaimUpdate` rows.
- Legacy sidecars are read as fallback only; canonical writes do not target sidecars.

### Entity registry

- Canonical write target: `.beads/index.json -> entities` and `.beads/index.json -> entity_aliases`.
- `sync_bead_entities_for_index(...)` runs during `add_bead_for_store(...)` and calls the entity judge over each bead.
- `upsert_canonical_entity(...)` updates aliases, confidence, provenance, and `updated_at`.

## Current idempotency and dedupe boundaries

### Finalized turn event idempotency

- `maybe_emit_finalize_memory_event(...)` computes an envelope hash.
- If the prior memory pass has the same `envelope_hash` and status `done` or `pending`, emission is skipped with `reason: idempotent_done`.
- If a prior pass is `done` but the envelope hash changes, a new event is emitted with `reason: turn_mutation` and metadata includes `supersedes_envelope_hash`.

### Memory pass claiming

- `try_claim_memory_pass(...)` claims only `pending` or retry-due `failed` passes.
- Claimed passes move to `running` under lock.
- This prevents concurrent direct processing of the same `{session_id, turn_id}` pass.

### Side-effect enqueue idempotency

- `enqueue_side_effect_event(...)` dedupes only against currently queued items by exact `idempotency_key`.
- For enrichment, the key is `enrich-{session_id}-{turn_id}`.
- Once a queued item is successfully processed and removed, the same idempotency key can be enqueued again by a later caller. Slice A needs a committed-state equality gate, not just queue-level dedupe.

### Bead creation dedupe

- `add_bead_for_store(...)` calls `_find_recent_duplicate_bead_id(...)` within `CORE_MEMORY_WRITE_DEDUP_WINDOW` before appending a new bead.
- This dedupe is content/window based and returns the existing bead id on duplicate.
- `_ensure_turn_creation_update(...)` ensures the update payload contains a current-turn creation row, but exact idempotency is ultimately dependent on store-level duplicate detection and source-turn shape.

### Association append dedupe

- `apply_crawler_updates(...)` builds `existing_assoc_keys` from `.beads/index.json` associations using `(source, target, relationship)`.
- It also tracks `queued_assoc_keys` within the current call.
- `merge_crawler_updates(...)` performs a second projection-time duplicate check before appending to index associations.
- Association ids are UUID-derived, so equality gates must compare canonical association content/dedupe keys, not raw ids.

### Promotion mark idempotency

- `apply_crawler_updates(...)` dedupes promotion ids within a payload but can append repeated `promotion_mark` rows to the crawler side log.
- `merge_crawler_updates(...)` only mutates the bead when `promotion_marked` is not already true.
- The effective lifecycle state is idempotent; the side-log is not a raw-byte equality surface.

### Claim and claim-update idempotency gaps

- `extract_and_attach_claims(...)` dedupes claims within the extracted batch via `dedup_claims(...)`.
- `write_claims_to_bead(...)` always appends normalized rows to `beads[bead_id].claims`; it does not dedupe against already persisted claims.
- `emit_claim_updates(...)` dedupes updates within one emission batch using `(decision, target_claim_id, replacement_claim_id, trigger_bead_id)`.
- `write_claim_updates_to_bead(...)` always appends normalized rows to `beads[bead_id].claim_updates`; it does not dedupe against already persisted claim updates.
- Claim ids and claim update ids may be generated per run, so Slice A needs stable per-output dedupe keys before replay can be considered safe.

### Entity idempotency

- `upsert_canonical_entity(...)` resolves by normalized aliases and canonical label.
- Aliases are merged into a stable canonical entity row.
- Provenance rows are deduped by `(kind, bead_id, source)`.
- `updated_at` changes on upsert, so equality gates must ignore raw timestamps and compare normalized registry/projection state.

## Window and context surfaces

`build_crawler_context(root, session_id, limit=200, carry_in_bead_ids=None)` currently captures:

- the last `limit` rows from `read_session_surface(root, session_id)`,
- `visible_bead_ids` as the union of those session row ids and any carry-in ids,
- natural-language `writing_contract`, `retrieval_contract`, `allowed_updates`, and `append_only_rules`.

Current gaps relative to the #9 gate:

- Window bounds are represented mostly as `limit=200`, not as an explicit lower/upper turn/bead range.
- The context does not persist a separate `window_context_ref` object with boundary metadata.
- Carry-in ids are included in `visible_bead_ids`, but their relationship to the bounded session slice is not explicit.
- Queued enrichment stores `crawler_visible_bead_ids` and `window_bead_ids`, but not the full bound definition used when the agent judged the update.
- Slice A should capture explicit bounds such as source session id, selected row count, first/last visible bead id, first/last visible turn id when available, carry-in ids, and the reason each carry-in was admitted.

## Overlapping semantic judgments today

Current #9 consolidation risk comes from multiple independently-authored semantic judgments:

1. `_default_crawler_updates(...)` and `_ensure_turn_creation_update(...)` use `judge_bead_fields(...)` for bead semantic fields.
2. `build_crawler_context(...)` asks a crawler agent for `beads_create`, `reviewed_beads`, and `associations`.
3. `apply_crawler_updates(...)` normalizes/validates association rows and queues promotion/association side-log rows.
4. `sync_bead_entities_for_index(...)` separately judges entities for each bead during `add_bead_for_store(...)`.
5. `extract_and_attach_claims(...)` separately extracts claims after bead creation.
6. `emit_claim_updates(...)` separately emits explicit or reconciled claim updates during decision/enrichment.
7. `run_session_decision_pass(...)` separately writes promotion lifecycle state.
8. Memory outcome classification writes outcome fields onto the canonical bead.

Slice A should not change those semantics yet. It should introduce a shared adapter shape that preserves their current committed effects while making provenance, context refs, confidence, and dedupe keys explicit.

## Slice A equality gate definition

Inline and queued enrichment should be considered equivalent when their canonical committed projections match after normalization, not when raw files are byte-identical.

Compare:

- canonical bead row content excluding volatile timestamps and generated ids where those are represented by stable dedupe keys,
- normalized association content keyed by `(source_bead, target_bead, relationship)` plus lifecycle state,
- promotion lifecycle state (`promotion_marked`, `promotion_state`, `promotion_locked`, visible ids),
- entity registry normalized labels, aliases, canonical ids, and provenance keys, excluding `updated_at`,
- claim/claim-update semantic content keyed by stable claim/update dedupe keys once Slice A adds them,
- `read_session_surface(...)` visible ids and normalized projection state.

Do not compare:

- raw JSON byte ordering,
- UUID-derived association ids,
- timestamps such as `created_at`, `updated_at`, `promotion_decided_at`,
- side-effect queue ids, lease tokens, attempts, retry timestamps,
- model diagnostics or transient invocation metadata unless explicitly included in the delta contract as stable version fields.

## Initial `session_enrichment_delta.v1` adapter implications

The behavior-preserving adapter should reserve, at minimum:

- top-level `schema: session_enrichment_delta.v1`, `session_id`, `turn_id`, `source`, and `provenance`,
- explicit `window_context_ref` with bounded window metadata,
- `evidence_context_fingerprint` and per-item `evidence_refs`,
- model/rubric/prompt version fields even before full validation exists,
- bounded arrays for bead creations, associations, association lifecycle actions, entity upserts/aliases, claims, claim updates, promotions, and memory outcome updates,
- stable per-output `dedupe_key` fields,
- monotonic or comparable sequencing keys for claim/lifecycle updates,
- strict normalization plus quarantine rows for invalid outputs.

For Slice A, the adapter should translate current subsystem outputs into this shared shape and then back into existing write calls. The committed state should remain behavior-preserving.

## Risk register for implementation chunks

1. **Claim replay duplication**: claim writes currently append without persisted dedupe. Address before claiming replay safety for `claims` or `claim_updates`.
2. **Queue idempotency is temporary**: `enrich-{session_id}-{turn_id}` only dedupes while the item is queued, not after successful drain.
3. **Window bounds are under-specified**: `limit=200` is not enough for reproducible judgment or equality tests.
4. **Side-log raw equality is misleading**: repeated promotion rows or UUID association ids can differ while final projection is equivalent.
5. **Entity timestamps are volatile**: upserts can change `updated_at` even when the canonical entity/alias state is semantically equivalent.
6. **Semantic policy is distributed**: #9 should unify output shape first, then fold entity/claim/goal semantics deliberately after adapter parity is proven.

## Compatibility and exact identifier notes

This analysis preserves these existing identifiers and treats them as compatibility anchors for the next chunk:

- `process_turn_finalized_impl` and public `process_turn_finalized` remain the turn-finalization entry path.
- `memory_engine` remains the sequence owner reported by the runtime response metadata.
- `USER_TURN` remains the default non-recursive origin for ordinary finalized turns.
- `_non_temporal_semantic_association_count` remains the current coverage helper used by the agent-authored semantic gate.
- `error_agent_updates_missing`, `error_agent_semantic_coverage_missing`, and the exact error text `agent-authored crawler updates required` remain current gate outputs.
- `merge_crawler_updates_for_flush` remains a compatibility wrapper that reports `authority_path: flush_merge_projection` while delegating to `merge_crawler_updates(...)`.
- `20251001` is intentionally retained as a checkpoint identifier from the #9 planning context and must not be lost in future compacted summaries.

## Required next chunk

Next implementation chunk should still be documentation/design-only unless this artifact is accepted:

1. Draft `session_enrichment_delta.v1` contract with bounded arrays and quarantine rules.
2. Define stable dedupe keys per item type.
3. Define the canonical projection comparator used by the Slice A equality gate.
4. Only then implement a behavior-preserving adapter layer.
