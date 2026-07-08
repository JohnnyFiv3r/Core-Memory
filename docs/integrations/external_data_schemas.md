# External Data Schemas — Ingest Host Contract

Core Memory does not own connectors. A connector host stores everything from a
system of record, decides on its configured trigger which data is bead-worthy,
and pushes a typed payload to Core Memory's ingest surface. Core Memory's job
is to provide the **distinct bead schemas** every connection type needs and to
guarantee the invariants (idempotency, versioning, current-truth retrieval,
grounding-gated confidence). This document is the schema contract a connector
builds against.

## The four knowledge categories

Most knowledge systems think in two categories — documents and structured
data. Companies actually run on four. A document *describes* reality, a table
*stores* reality, an operational system records reality *changing*, and
analysis *interprets* reality.

| Category | Bead type | `data_type_flag` | The bead anchors… |
|---|---|---|---|
| Conversations | `transcript` | `transcript` | a conversation in a source store |
| Documents | `document_reference` | `document` / `media` | a document or media artifact |
| Structured data | `structured_observation` | `relational` | a record/metric as it is (stored state) |
| Operational systems | `operational_event` | `operational_event` | a record changing (state transition) |
| *(interpretive)* | `state_assertion` | `state_assertion` | a derived current state |
| *(interpretive)* | `data_insight` | `data_insight` | an analytical finding |

## The three schemas the host selects

The connector host passes the schema to use — **document, data, or transcript** — and Core
Memory routes by `data_type_flag` (or explicit `type`). All three are present
with distinct required-field validation and a dedicated entry point:

| Host schema | Entry point | Required fields (beyond title/summary) |
|---|---|---|
| **document** | `ingest_document_reference` | `source_id`, `source_event_id`, `source_system`, `core_memory_unifying_id`, `hydration_ref`, `document_name`, and `document_id` or `raw_source_object_id` |
| **data** | `ingest_structured_observation` (also `ingest_operational_event`, `ingest_state_assertion`) | `source_id`, `source_event_id`, `source_system`, `core_memory_unifying_id`, `hydration_ref`, `source_table`, `source_record_id`, `as_of_timestamp`/`observed_at`, entities |
| **transcript** | `data_type_flag: "transcript"` via `ingest_external_evidence` (or `ingest_transcript` for full turn ingestion) | `source_id`, `source_event_id`, `source_system`, `core_memory_unifying_id`, `hydration_ref`, and `message_refs` or `source_turn_ids` |

All routes are idempotent (re-delivery of a seen `source_event_id` returns
`already_exists`), version-aware (a changed document/record with a new event id
writes a new version and supersedes the prior — except operational events,
which accumulate), and write source anchors only — bodies stay in the
caller-owned store, reached via `hydration_ref`.

## The boundary that matters: stored state vs. state transition

The one place connectors route inconsistently is **`structured_observation`
vs. `operational_event`**. The rule:

- **`structured_observation`** — a record or metric *as it currently is*. "COGS
  is $12,450 for this period." A snapshot of stored state. A newer snapshot of
  the *same* record **supersedes** the old one (current-truth wins).
- **`operational_event`** — a record *changing*. "Deal 991 moved Discovery →
  Proposal." A transition. Sibling transitions of the same business object
  **accumulate** — they are history and never supersede each other. This is the
  worldline substrate (`Event → Decision → Action → Outcome`).
- **`state_assertion`** — a *derived* current state interpreted from the above.
  "Fresh Produce LLC became the primary COGS driver." Supersedes as the
  derivation is re-run.

A practical test: *if I ingest this twice for the same object, do I want one
current row or a growing history?* One current row → `structured_observation`.
A history → `operational_event`.

## Operational event systems (the fourth category)

These all produce `Event → Decision → Action → Outcome`, which is exactly what
worldlines, tension surfaces, and causal traversal sit on top of:

| Domain | Examples | Natural worldline |
|---|---|---|
| Product dev | GitHub, Jira, Linear, GitLab | Problem → Discussion → Decision → Implementation → Outcome |
| Support | Zendesk, Intercom, Freshdesk | Complaint → Escalation → Ticket → Fix → Resolution |
| Sales | HubSpot, Salesforce, Pipedrive | Lead → Discovery → Proposal → Negotiation → Win/Loss |
| Incident | PagerDuty, Opsgenie, incident.io | Alert → Investigation → Mitigation → Root Cause → Postmortem |
| DevOps | Vercel, Datadog, CloudWatch | What changed → what broke → who → when |
| Restaurant | Toast, Square, 7shifts | Orders / Labor / Inventory → Food Cost |
| Industrial | Ignition, AVEVA PI, FactoryTalk | Alarm → Operator Action → Process Change → Compliance Impact |

`operational_event` fields: `business_object_type`, `business_object_id`,
`record_action`, `actor`, `state_change` ({from, to}), `as_of_timestamp` /
`occurred_at`, plus standard entities and source attribution.

## Not a bead type: continuous telemetry

Raw time-series streams (SCADA tags, Datadog metric streams) are **not** beads.
Only the bead-worthy events derived from them are: a threshold breach is an
`operational_event` (alarm), an anomaly is a `data_insight`, a reading of
record is a `structured_observation`. Keeping streams out is the entire point
of the host-side admission layer — Core Memory anchors what warrants memory,
not every measurement.
