# Satorid External Evidence Contract

Status: Experimental P0

This contract lets product orchestrators such as Satorid write typed, source-attributed memory anchors without copying raw source bodies into Core Memory.

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
  "source_id": "Satorid source UUID",
  "source_event_id": "Satorid source event UUID",
  "source_system": "quickbooks | upload | ragie | slack | snowflake | supabase",
  "core_memory_unifying_id": "Stable cross-store join key",
  "hydration_ref": {
    "store": "satorid | ragie | supabase | snowflake",
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

- `document_id` or `ragie_document_id`
- `document_name`

Recommended:

- `raw_source_object_id`
- `mime_type`
- `document_kind`
- `document_date`
- `author_or_owner`
- `section_refs`

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
