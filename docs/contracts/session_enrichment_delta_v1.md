# session_enrichment_delta.v1 contract

Status: active Slice A adapter contract for #9. Runtime paths project queued/inline enrichment outputs into this shape for the Slice A row types only.

## Purpose

`session_enrichment_delta.v1` is the shared envelope for one session-window enrichment judgment. Slice A uses it as an adapter shape around existing write paths so inline and queued enrichment can converge on equivalent committed state before any semantic subsystem is folded into a unified judge.

The contract is not a new semantic policy owner. Semantic policy stays in the existing Core Memory runtime and domain modules. OpenClaw/plugin bridge code must not own relationship, claim, entity, or promotion semantics.

The adapter supports both current execution modes: queued enrichment when `CORE_MEMORY_ENRICHMENT_QUEUE` is enabled and inline fallback when the queue is disabled.

## Design goals

1. Preserve existing behavior while introducing a shared shape.
2. Make every output row replay-safe through stable `dedupe_key` fields.
3. Make window bounds, provenance, evidence refs, confidence, and versioning explicit.
4. Normalize strictly and quarantine invalid rows instead of silently applying ambiguous data.
5. Support later folding of #3 associations, #8 entity upserts/aliases, claims, #2 goal lifecycle, #5 grounding validation, and #6 evals without a contract break.

## Top-level envelope

```json
{
  "schema": "session_enrichment_delta.v1",
  "session_id": "string",
  "turn_id": "string",
  "source": {
    "kind": "inline|queued|agent_callable|metadata|fallback|test",
    "authority_path": "canonical_in_process|turn-enrichment|flush_merge_projection|other",
    "origin": "USER_TURN|MEMORY_PASS|other",
    "queue_id": "string|null",
    "idempotency_key": "enrich-{session_id}-{turn_id}|string|null"
  },
  "contract_versions": {
    "delta": "session_enrichment_delta.v1",
    "rubric": "string",
    "prompt": "string",
    "model": "string",
    "normalizer": "string"
  },
  "window_context_ref": {
    "session_id": "string",
    "selection_reason": "turn_finalization|queued_enrichment|test|other",
    "limit": 200,
    "row_count": 0,
    "first_visible_bead_id": "string|null",
    "last_visible_bead_id": "string|null",
    "first_visible_turn_id": "string|null",
    "last_visible_turn_id": "string|null",
    "visible_bead_ids": ["string"],
    "window_turn_ids": ["string"],
    "carry_in_bead_ids": ["string"],
    "carry_in_reasons": {"bead-id": "retrieved_context|explicit_input|test|other"},
    "context_fingerprint": "sha256:string"
  },
  "provenance": {
    "producer": "core_memory.runtime|agent|test|other",
    "run_id": "string|null",
    "trace_id": "string|null",
    "transaction_id": "string|null",
    "created_at": "iso8601"
  },
  "beads_create": [],
  "promotions": [],
  "associations": [],
  "association_lifecycle": [],
  "entity_upserts": [],
  "claims": [],
  "claim_updates": [],
  "goal_lifecycle": [],
  "memory_outcomes": [],
  "diagnostics": {}
}
```

## Bounded arrays

All row arrays are bounded before normalization. Slice A defaults:

- `beads_create`: max 4
- `promotions`: max 64
- `associations`: max 256
- `association_lifecycle`: max 128
- `entity_upserts`: reserved; accepted count remains 0 in Slice A
- `claims`: reserved; accepted count remains 0 in Slice A
- `claim_updates`: reserved; accepted count remains 0 in Slice A
- `goal_lifecycle`: reserved; accepted count remains 0 in Slice A
- `memory_outcomes`: reserved; accepted count remains 0 in Slice A

Rows over the bound are quarantined with reason `array_bound_exceeded`. The accepted prefix remains processable.

## Common row fields

Every emitted row type includes these common fields after normalization:

```json
{
  "dedupe_key": "string",
  "confidence": 0.0,
  "provenance": {
    "kind": "model_inferred|heuristic|explicit_user|system|fallback|test",
    "source": "string",
    "bead_id": "string|null",
    "turn_id": "string|null"
  },
  "evidence_refs": [
    {
      "kind": "turn|bead|claim|association|tool|text_span|context_fingerprint",
      "id": "string",
      "field": "string|null",
      "quote": "string|null",
      "hash": "sha256:string|null"
    }
  ],
  "context_fingerprint": "sha256:string",
  "sequence_key": "string|null",
  "rationale": "string|null"
}
```

Common rules:

- `dedupe_key` is required for all row types.
- `confidence` is required and clamped to `[0.0, 1.0]`.
- `evidence_refs` must contain at least one grounding ref unless `provenance.kind` is `fallback` or `test`.
- `context_fingerprint` should match `window_context_ref.context_fingerprint` unless the row explicitly points to a narrower evidence context.
- `rationale` is diagnostic support, not semantic policy.

## Row schemas

### `beads_create[]`

Adapter target today: existing `beads_create` rows passed to `apply_crawler_updates(...)` and ultimately `MemoryStore.add_bead(...)`.

Required fields:

```json
{
  "dedupe_key": "bead:{session_id}:{turn_id}:{normalized_type}:{content_hash}",
  "type": "context|decision|design_principle|goal|failed_hypothesis|constraint|other",
  "title": "string",
  "summary": ["string"],
  "detail": "string|null",
  "session_id": "string",
  "source_turn_ids": ["string"],
  "source_turn_ref": {},
  "tags": ["string"],
  "entities": ["string"],
  "topics": ["string"],
  "because": ["string"],
  "supporting_facts": ["string"],
  "evidence_refs": [],
  "retrieval_eligible": false,
  "retrieval_title": "string|null",
  "retrieval_facts": ["string"],
  "state_change": "string|null",
  "validity": "string|null",
  "effective_from": "string|null",
  "effective_to": "string|null",
  "observed_at": "string|null",
  "prev_bead_id": "string|null",
  "turn_index": 0
}
```

Normalization rules:

- `source_turn_ids` must include the current `turn_id` for the canonical current-turn bead.
- Retrieval eligibility is downgraded to false if structured retrieval payload is insufficient.
- Structural fields may be filled by the adapter; semantic fields remain judged by existing runtime logic during Slice A.

### `promotions[]`

Adapter target today: `promotion_mark` side-log rows and `decide_session_promotion_states(...)` projection writes.

```json
{
  "dedupe_key": "promotion:{session_id}:{bead_id}:{promotion_scope}",
  "bead_id": "string",
  "promotion_scope": "rolling_continuity|session_decision|goal_lifecycle",
  "desired_state": "marked|candidate|null|promoted",
  "reason_text": "string|null"
}
```

Rules:

- Promotion mark rows are monotonic when targeting `promotion_marked=true`.
- Equality gates compare final lifecycle state, not raw side-log rows.

### `associations[]`

Adapter target today: `associations` rows passed to `apply_crawler_updates(...)` and later merged into `.beads/index.json`.

```json
{
  "dedupe_key": "assoc:{source_bead_id}:{target_bead_id}:{relationship}",
  "source_bead_id": "string",
  "target_bead_id": "string",
  "relationship": "supports|refines|caused_by|enables|diagnoses|resolves|supersedes|contradicts|follows|precedes|related_to|other-canonical",
  "relationship_raw": "string|null",
  "reason_text": "string",
  "reason_code": "string|null",
  "evidence_fields": ["string"],
  "edge_class": "agent_judged|heuristic|temporal|fallback"
}
```

Rules:

- Relationship labels are canonicalized through the existing association inference policy.
- Invalid rows are quarantined, not applied.
- The source bead must be session-local.
- The target bead must be in `window_context_ref.visible_bead_ids` unless `association_scope=historical_session` is explicitly set by the adapter.

### `association_lifecycle[]`

Adapter target today: `association_lifecycle` rows in crawler updates.

```json
{
  "dedupe_key": "assoc-life:{association_id}:{action}:{replacement_association_id|null}:{sequence_key}",
  "association_id": "string",
  "action": "retract|supersede|reaffirm",
  "replacement_association_id": "string|null",
  "reason_text": "string|null",
  "sequence_key": "assoc-life:{session_id}:{turn_id}:{ordinal}"
}
```

Rules:

- `supersede` requires a valid replacement association when provided.
- Lifecycle actions must remain scoped to the same session.

### `entity_upserts[]`

Reserved for #8 folding. Slice A records input counts diagnostically but does not accept or project these rows.

```json
{
  "dedupe_key": "entity:{normalized_label}",
  "label": "string",
  "normalized_label": "string",
  "aliases": ["string"],
  "entity_kind": "person|org|project|place|concept|artifact|other",
  "source_bead_id": "string|null"
}
```

Rules:

- Alias normalization decides canonical identity.
- Equality gates compare normalized labels, aliases, confidence max, and provenance keys; ignore `updated_at`.

### `claims[]`

Reserved for later claim folding. Slice A records input counts diagnostically but does not accept or project these rows.

```json
{
  "dedupe_key": "claim:{subject_norm}:{slot_norm}:{value_hash}:{source_bead_id}",
  "id": "string|null",
  "subject": "string",
  "slot": "string",
  "value": "any-json",
  "source_bead_id": "string",
  "source_turn_ids": ["string"],
  "valid_from": "string|null",
  "valid_to": "string|null"
}
```

Rules:

- Slice A must not claim replay safety for claims until persisted claim writes dedupe by `dedupe_key` or an equivalent canonical identity.
- Generated `id` must not be the equality key.

### `claim_updates[]`

Reserved for later claim-update folding. Slice A records input counts diagnostically but does not accept or project these rows.

```json
{
  "dedupe_key": "claim-update:{decision}:{target_claim_key}:{replacement_claim_key|null}:{trigger_bead_id}:{sequence_key}",
  "id": "string|null",
  "decision": "reaffirm|supersede|retract|conflict",
  "target_claim_id": "string|null",
  "target_claim_key": "string",
  "replacement_claim_id": "string|null",
  "replacement_claim_key": "string|null",
  "subject": "string",
  "slot": "string",
  "trigger_bead_id": "string",
  "reason_text": "string",
  "sequence_key": "claim-update:{session_id}:{turn_id}:{ordinal}"
}
```

Rules:

- Invalidating decisions require `trigger_bead_id`.
- Claim update ordering is by `sequence_key`, not UUID.
- Equality gates compare semantic target/replacement keys and lifecycle decision, not generated ids.

### `goal_lifecycle[]`

Reserved for #2 folding. Slice A records input counts diagnostically but does not accept or project these rows.

```json
{
  "dedupe_key": "goal-life:{goal_key}:{action}:{sequence_key}",
  "goal_bead_id": "string|null",
  "goal_key": "string",
  "action": "open|progress|blocked|complete|abandon|reopen",
  "reason_text": "string|null",
  "sequence_key": "goal-life:{session_id}:{turn_id}:{ordinal}"
}
```

### `memory_outcomes[]`

Reserved for later memory-outcome folding. Slice A records input counts diagnostically but does not accept or project these rows.

```json
{
  "dedupe_key": "memory-outcome:{bead_id}:{turn_id}",
  "bead_id": "string",
  "interaction_role": "string|null",
  "memory_outcome": {}
}
```

## Quarantine contract

Invalid rows are written to a quarantine surface with enough context to debug and replay after policy fixes.

Proposed row:

```json
{
  "schema": "session_enrichment_delta.quarantine.v1",
  "delta_schema": "session_enrichment_delta.v1",
  "session_id": "string",
  "turn_id": "string",
  "row_type": "associations|claims|entity_upserts|...",
  "dedupe_key": "string|null",
  "reasons": ["string"],
  "warnings": ["string"],
  "normalized_record": {},
  "original_record": {},
  "window_context_ref": {},
  "created_at": "iso8601"
}
```

Default path for Slice A: `.beads/events/session-enrichment-delta-quarantine.jsonl`.

Existing `association-quarantine.jsonl` remains supported while association writes still flow through the existing association contract. The delta quarantine can either mirror association quarantine rows or become the only quarantine writer once the adapter owns normalization.

## Canonical projection equality gate

Inline and queued enrichment are equal when the normalized committed state is equal. The comparator should build a canonical projection from `.beads/index.json` and `.beads/session-{session_id}.jsonl` and compare:

- visible session bead ids and canonical current-turn bead identity,
- normalized bead content excluding volatile timestamps and generated ids,
- associations by `dedupe_key` / `(source_bead, target_bead, relationship)`,
- association lifecycle state by target association and action sequence,
- promotion lifecycle state, including `promotion_marked`, `promotion_state`, `promotion_locked`, and `promotion_decision`,
- entity registry by normalized label, aliases, canonical id, confidence, and provenance keys, excluding `updated_at`,
- claims by stable claim dedupe key,
- claim updates by stable claim-update dedupe key and sequence,
- memory outcome fields by `bead_id` and `turn_id`.

The comparator must ignore:

- raw JSON byte order,
- UUID-derived ids when stable dedupe keys exist,
- timestamps (`created_at`, `updated_at`, `promotion_decided_at`, side-effect queue timestamps),
- side-effect queue ids, leases, attempts, and retry metadata,
- transient model diagnostics not part of `contract_versions`.

## Slice A adapter sequence

1. Capture current subsystem outputs and normalize them into `session_enrichment_delta.v1`.
2. Validate bounds, required common fields, canonical enums, visible-scope rules, and confidence ranges.
3. Quarantine invalid rows.
4. Convert accepted Slice A rows through crawler/delta side effects:
   - `beads_create`, `promotions`, `associations`, and `association_lifecycle` flow through `apply_crawler_updates(...)` plus `merge_crawler_updates(...)` paths.
   - `entity_upserts`, `claims`, `claim_updates`, `goal_lifecycle`, and `memory_outcomes` stay reserved/diagnostic-only until their owning slices are folded.
5. Use the canonical projection equality gate to prove inline and queued enrichment equivalence.

## Acceptance notes for #9 Slice A

- Re-running the same turn/enrichment job must not duplicate Slice A associations or promotion marks in the final canonical projection.
- Existing tests and behavior must remain preserved.
- Window bounds must be explicit and test-covered.
- Every accepted row must carry provenance, evidence/context refs, confidence, and a stable dedupe key.
- Invalid rows must be quarantined with reasons and original payload context.
