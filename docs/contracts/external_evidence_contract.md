# External Evidence Contract

Status: Experimental P0

This contract lets product orchestrators write typed, source-attributed memory anchors without copying raw source bodies into Core Memory.

## Write Surfaces

Python:

```python
from core_memory import ingest_external_evidence
from core_memory import ingest_structured_observation
from core_memory import ingest_document_reference
from core_memory import ingest_state_assertion
```

HTTP:

```text
POST /v1/memory/external-evidence
POST /v1/memory/structured-observation
POST /v1/memory/document-reference
POST /v1/memory/state-assertion
```

`/v1/memory/external-evidence` routes by `data_type_flag`:

- `conversation.transcript` -> `transcript`
- `document`, `media`, `document.media` -> `document_reference`
- `relational`, `relational.data`, `structured_observation` -> `structured_observation`
- `state_assertion`, `derived_business_state`, `document_claim`, `document_observation` -> `state_assertion`

## Shared Required Fields

```json
{
  "title": "Short memory title",
  "summary": ["One to three compact factual bullets"],
  "source_id": "Source UUID",
  "source_event_id": "Source event UUID",
  "source_system": "quickbooks | upload | slack | snowflake | supabase | custom",
  "core_memory_unifying_id": "Stable cross-store join key",
  "hydration_ref": {
    "store": "upload | supabase | snowflake | custom",
    "ref": "Store-native ID or URI"
  }
}
```

Core Memory stores semantic handles and hydration pointers. It should not store complete document bodies, relational rows, connector payloads, or file bytes by default.

## Structured Observation

Required in addition to shared fields:

- `source_table`
- `source_record_id`
- `as_of_timestamp` or `observed_at`
- `entities` or `entity_refs`

Recommended:

- `record_action`
- `record_grain`
- `business_object_type`
- `business_object_id`
- `metric_name`
- `metric_value`
- `currency`
- `attribute_tags`

## Document/Media Reference

Required in addition to shared fields:

- `document_id` or `raw_source_object_id`
- `document_name`

Recommended:

- `raw_source_object_id` when it differs from `document_id`
- `mime_type`
- `document_kind`
- `document_date`
- `author_or_owner`
- `section_refs` for section- or chunk-scoped beads

Multiple `document_reference` beads may carry the same `document_id` when they
refer to different `section_refs`. Core Memory treats the section scope as part
of document-reference identity: whole-document beads version against
whole-document beads, and a given section versions against that same section,
but sibling sections of the same document coexist.

Historical beads may still contain `ragie_document_id`; Core Memory keeps
read-tolerance for those records, but new document-reference writes should use
`document_id` / `raw_source_object_id`.

## State Assertion

`state_assertion` is an interpreted or derived bead. Use it for business state, document-derived assertions, or analytical conclusions. Keep `structured_observation` factual; put interpretation here.

Required:

- `title`
- `summary`
- `derived_from`, `derived_from_bead_ids`, or `evidence_refs`
- `effective_from` or `observed_at`

Recommended:

- `assertion_kind` such as `business_state`, `document_claim`, or `derived_analysis`
- `assertion_subject`
- `assertion_predicate`
- `assertion_value`
- `authority = "derived_analysis"`
- `confidence`

Example:

```json
{
  "type": "state_assertion",
  "title": "Fresh Produce LLC became the primary COGS driver",
  "summary": ["Fresh Produce LLC accounted for 61% of the COGS increase during the measured period."],
  "derived_from": ["structured_observation:cogs_spike_2026_05_04"],
  "assertion_kind": "business_state",
  "assertion_subject": "Fresh Produce LLC",
  "assertion_predicate": "became_primary_driver_of",
  "assertion_value": "COGS increase",
  "effective_from": "2026-05-04T00:00:00Z",
  "confidence": 0.82,
  "authority": "derived_analysis"
}
```

## Receipt

Successful writes return:

```json
{
  "ok": true,
  "accepted": true,
  "status": "accepted",
  "bead_id": "bead-...",
  "bead_ids": ["bead-..."],
  "created_count": 1,
  "event_id": "evt-...",
  "type": "structured_observation | document_reference | state_assertion"
}
```

Repeated writes with the same `source_event_id` return `status: "already_exists"` and the existing bead id.

## Source version supersession

Beads are immutable. When a known source object (same `source_id` +
`document_id` plus section scope, `source_record_id`, or `transcript_id`)
arrives with a **new** `source_event_id` and **changed** content, the ingest
path writes a new version bead and closes the prior one — it never edits in
place:

- the new bead carries `supersedes: ["<prior bead id>"]`
- the prior bead gets `status: "superseded"`, `superseded_by`, and
  `effective_to` (its validity window closes)
- a `supersedes` association (new → old) is written so provenance surfaces
  can walk the version chain
- the receipt returns `status: "version_superseded"` with
  `superseded_bead_id`

Idempotency rules, in order:

1. Same `source_event_id` as any existing version (current or superseded) →
   `already_exists`. Event re-delivery never creates churn.
2. New `source_event_id` but identical content → `already_exists`.
3. New `source_event_id` and changed content → new version, prior superseded.

Retrieval returns current truth only: superseded versions are excluded from
the visible corpus unless a caller passes `include_superseded: true`
(provenance reporting).

Newly accepted external-evidence beads also request source-local association
coverage. Coverage generates candidate association proposals first; active
association edges are written only after a semantic/judge decision approves
them. Receipts include:

- `association_run_id`
- `association_trigger`
- `association_state`
- `association_queued`

`association_state` is `pending_judge` when no judge is configured yet, and may
be `judge_failed` when a configured judge errors. These states mean the bead was
written but graph association coverage is not yet complete; they must not be
treated as `linked`.

Replayed `already_exists` events do not enqueue another association run. Changed
source-object versions still write the new bead and let coverage generate
judge-reviewed candidates for version lineage.

## Source-ingest envelope

Connectors may include `source_ingest_envelope` on any external-evidence write
to identify the coherent import/capture boundary that produced the bead. Core
Memory also derives a best-effort envelope from existing source fields when this
object is omitted.

Useful fields:

```json
{
  "boundary_type": "DocumentImported | MediaImported | TranscriptCaptured | StructuredDatasetImported | RelationalSnapshotCommitted | RelationalDeltaCommitted | AgentArtifactCaptured | OperationalEventCaptured | StateAssertionCaptured",
  "ingest_batch_id": "stable-source-batch-id",
  "workspace_id": "host workspace id",
  "source_type": "google_drive",
  "source_object_id": "provider object id",
  "source_version": "etag / sha / version",
  "source_uri": "hydration or provider URI",
  "source_event_id": "provider delivery/event id",
  "actor_id": "optional user or app actor",
  "agent_id": "optional agent/runtime actor",
  "timestamp": "2026-06-17T00:00:00Z",
  "authority_class": "source_attributed",
  "hydration_refs": [{"store": "supabase", "ref": "raw/doc_001"}],
  "parent_artifact": {"document_id": "doc_001"},
  "local_refs": {"section_refs": [{"section_id": "security"}]}
}
```

Core Memory persists the full envelope on the bead and propagates compact
`source_ingest_envelope_ref` values through association coverage runs,
candidate rows, judge receipts, and accepted associations. Deterministic
source-local structure, such as document sections, row order, transcript
continuity, or supersession, is treated as candidate evidence and provenance,
not active semantic graph truth. Active edges are still written only by the
association judge path.

Association coverage callers may also pass `source_ingest_envelope_refs` to
`POST /v1/memory/association-runs` or through the governed `maintain()` action
when the host already has compact envelope refs from earlier writes. Core
Memory merges those refs with bead-local refs and carries them into queued
side-effect runs, candidate rows, and judge context.
