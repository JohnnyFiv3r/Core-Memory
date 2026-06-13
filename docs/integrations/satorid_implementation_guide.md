# Satorid → Core Memory: Implementation Guide for Codex

**Audience:** the Codex agent building Satorid.
**Goal:** implement Satorid connectors that store everything from a system of
record and, on a defined trigger, push only the bead-worthy data to Core Memory.

This is a contract guide. Core Memory provides the schemas and invariants;
Satorid owns the connectors, storage, triggers, and user configuration. **Do
not add connector code to the Core Memory repo** — connectors live in Satorid
and talk to Core Memory over the HTTP API documented here.

---

## 1. Responsibility split

| Concern | Owner |
|---|---|
| Connecting to systems of record (GitHub, Jira, HubSpot, Zendesk, PagerDuty, Toast, SCADA, …) | **Satorid** |
| Storing raw events/records/documents | **Satorid** (it is the hydration backend) |
| Trigger definitions (when to push) | **Satorid** |
| Per-connector user config (what may be used) | **Satorid** |
| Deciding which events are bead-worthy | **Satorid** (admission policy) |
| Normalizing a source event into a bead payload | **Satorid** |
| Writing the bead, idempotency, versioning, retrieval, governance | **Core Memory** |

Core Memory never reaches back into a system of record. Satorid pushes a
**semantic anchor** (title, summary, structured fields, and a `hydration_ref`
back to where the body lives). Bodies — document contents, full rows, ticket
threads — stay in Satorid.

---

## 2. The four knowledge categories

Most systems think in documents + structured data. Companies run on four
categories. Map every connector's data to one:

| Category | Core Memory bead type | `data_type_flag` | Anchors |
|---|---|---|---|
| Conversations | `transcript` | `transcript` | a conversation/thread |
| Documents | `document_reference` | `document` / `media` | a doc or media artifact |
| Structured data | `structured_observation` | `relational` | a record/metric **as it is** (stored state) |
| Operational systems | `operational_event` | `operational_event` | a record **changing** (state transition) |
| *(interpretive)* | `state_assertion` | `state_assertion` | a derived current state |
| *(interpretive)* | `data_insight` | `data_insight` | an analytical finding |

**The boundary that matters most** — `structured_observation` vs
`operational_event`:

- One current row wanted on re-ingest → `structured_observation` (a newer
  snapshot **supersedes** the old).
- A growing history wanted → `operational_event` (sibling transitions
  **accumulate**; they are the worldline substrate `Event → Decision → Action →
  Outcome` and never supersede each other).

Most operational-system connectors (GitHub, Jira, HubSpot, Zendesk, PagerDuty)
produce primarily `operational_event` beads plus the occasional
`document_reference` (release notes, attachments) and `transcript` (logged
calls).

---

## 3. Transport: the HTTP API

Satorid is a separate service, so use the HTTP surface (not the Python API).

**Base + auth.** Set `CORE_MEMORY_HTTP_TOKEN` on the Core Memory server. Send it
on every request as either `Authorization: Bearer <token>` or
`X-Memory-Token: <token>`. In hosted mode the server owns the root; do **not**
send a `root` unless you control it. Multi-tenant: send `X-Tenant-Id: <id>`.

### Ingest endpoints (one per schema)

| Endpoint | Schema | Helper for |
|---|---|---|
| `POST /v1/memory/operational-event` | operational_event | state transitions |
| `POST /v1/memory/structured-observation` | structured_observation | records/metrics |
| `POST /v1/memory/document-reference` | document_reference | documents/media |
| `POST /v1/memory/state-assertion` | state_assertion | derived state |
| `POST /v1/memory/external-evidence` | any (routes by `data_type_flag`/`type`) | transcript + generic |

Request body is the **payload fields directly** (the server merges extra keys),
plus optional `session_id` (defaults to `"external"`). Example:

```http
POST /v1/memory/operational-event
Authorization: Bearer $CORE_MEMORY_HTTP_TOKEN
Content-Type: application/json

{
  "session_id": "hubspot-source",
  "title": "Deal 991 moved to Proposal",
  "summary": ["Deal 991 (Acme Corp) advanced Discovery → Proposal."],
  "source_id": "hubspot:portal-1",
  "source_event_id": "hs-evt-001",
  "source_system": "hubspot",
  "business_object_type": "deal",
  "business_object_id": "991",
  "record_action": "stage_changed",
  "actor": "rep@acme.com",
  "state_change": {"from": "discovery", "to": "proposal"},
  "entities": ["Acme Corp"],
  "as_of_timestamp": "2026-06-12T15:00:00Z",
  "core_memory_unifying_id": "hubspot:deal:991",
  "hydration_ref": {"store": "satorid", "ref": "hubspot/deals/991/events/001"}
}
```

### Receipt statuses

Every ingest returns a receipt. Branch on `status`:

| `status` | Meaning | Satorid action |
|---|---|---|
| `accepted` | new bead written | record `bead_id` |
| `already_exists` | same `source_event_id` or identical content | no-op (safe replay) |
| `version_superseded` | changed doc/record → new version; prior closed | record new `bead_id`, note `superseded_bead_id` |
| `skipped` *(source-event path)* | event not bead-worthy | record reason, move on |
| HTTP 400 | schema validation failure | fix payload; do not retry as-is |

---

## 4. Required fields per schema

All external schemas require `title` and `summary`, and (except
`state_assertion`) the source-attribution quartet plus a `hydration_ref`:
`source_id`, `source_event_id`, `source_system`, `core_memory_unifying_id`,
`hydration_ref`.

| Schema | Additional required |
|---|---|
| `document_reference` | `document_name`, and `document_id` or `ragie_document_id` |
| `structured_observation` | `source_table`, `source_record_id`, `as_of_timestamp` (or `observed_at`), `entities` (or `entity_refs`) |
| `operational_event` | `record_action`, `business_object_id` (or `source_record_id`), `as_of_timestamp`/`occurred_at`/`observed_at`, `entities` (or `entity_refs`) |
| `transcript` | `message_refs` or `source_turn_ids` |
| `state_assertion` | `derived_from`/`derived_from_bead_ids`/`evidence_refs`, `effective_from`/`observed_at` |

Identity fields drive the invariants — get them stable:

- **`source_event_id`** — the provider's delivery/event id. Core Memory dedupes
  on it, so webhook re-delivery is safe. Make it unique per real event.
- **`source_record_id` / `document_id` / `business_object_id`** — stable object
  identity. A *changed* document/record with the **same** object id and a **new**
  `source_event_id` versions (supersedes); operational events with the same
  object id accumulate.
- **`core_memory_unifying_id`** — opaque cross-store join key. Give the same id
  to related items across connectors (e.g. a Zoom transcript and a HubSpot deal
  for the same meeting) so retrieval can group them.
- **`hydration_ref`** — `{store, ref}` pointing back into Satorid so Core Memory
  can resolve the body on demand.

---

## 5. Grounding (set it per connector)

Each payload may set `grounding` — how the data is known. It gates the bead's
confidence class:

| `grounding` | Use for | Class effect |
|---|---|---|
| `observed` | primary records from a system of record (default for the external types) | enters at **B** |
| `extracted` | a value parsed out of a document/field | enters at **B** |
| `inferred` | something Satorid computed/derived | enters at C |
| `speculative` | an untested guess | capped at B |

For raw source events, leave it unset — the bead type defaults
`operational_event`/`structured_observation`/`document_reference`/`transcript`
to `observed`. Set it explicitly only when you parse or derive.

---

## 6. The admission layer (which events warrant beads)

This is Satorid's core logic. Core Memory ships a reference engine
(`runtime/ingest/source_events.py`: `SourceEventMapping` + `SourceEventRule`)
and a worked GitHub example (`integrations/github/connector.py`) — **read them
as the pattern, then implement the equivalent in Satorid.**

The bar for a rule: *would an agent ever cite this event as evidence, context,
or cause?* Admit records of change; skip workflow noise.

| System | Admit → bead | Skip |
|---|---|---|
| GitHub | merged PR, closed issue, published release, default-branch doc change | unmerged closes, label/assign, bot actors, feature-branch pushes |
| Jira | status → terminal transition, epic description edit | comment churn, field recalcs |
| HubSpot | deal stage change, closed-won/lost, attachment | property recalculations |
| Zendesk | escalation, ticket resolution | auto-replies, view changes |
| PagerDuty | alert, mitigation, postmortem | heartbeat/flap noise |

**Never** make raw telemetry (SCADA tags, Datadog streams) into beads. Only the
derived bead-worthy events are: a threshold breach → `operational_event`, an
anomaly → `data_insight`, a reading of record → `structured_observation`.

---

## 7. User configuration: withholding data

Users grant or withhold categories of data per connector. The rule is simple:
**if the user withholds a data class, Satorid must not build a payload for it.**
Enforce this in Satorid's admission layer *before* the HTTP call — Core Memory
has no visibility into what was withheld and will write whatever it receives.

Recommended config shape (Satorid-side):

```yaml
connectors:
  hubspot:
    enabled: true
    allow:
      operational_event: true     # deal stage changes
      document_reference: true     # attachments
      transcript: false            # withhold logged call transcripts
    fields_excluded: ["contact.email", "deal.amount"]   # strip before send
```

When a class is `false`, the matching admission rules return no payload. When a
field is excluded, strip it from `summary`/`detail`/structured fields before
sending. This is the user's privacy boundary; honor it in Satorid.

---

## 8. Approval (human-in-the-loop)

Auto-written beads can be gated for human review. Two ways to enter the queue:

1. Set `approval_status: "pending"` on the payload at ingest (e.g. for a
   policy-sensitive connector or low-confidence event).
2. Call `POST /v1/memory/request-approval` with `{bead_id}` after the fact.

Review surface:

| Action | Endpoint |
|---|---|
| list queue | `GET /v1/memory/pending-approvals` |
| approve | `POST /v1/memory/approve` `{bead_id, approver, note}` |
| reject | `POST /v1/memory/reject` `{bead_id, approver, reason}` |

`pending` beads stay retrievable (a signal, not a quarantine). `approved` →
confidence class A. `rejected` → excluded from retrieval but retained for audit.
Satorid should surface the pending queue in its UI for the reviewing user.

---

## 9. Invariants Core Memory guarantees (so Satorid doesn't reimplement them)

- **Idempotency** on `source_event_id` — push freely; replays are no-ops.
- **Versioning** — a changed document/record supersedes its prior version; old
  versions leave current-truth retrieval but remain for provenance.
- **Accumulation** — operational events of the same object coexist as history.
- **Current-truth retrieval** — superseded/rejected beads are excluded from
  recall by default.
- **Grounding-gated confidence (C/B/A)** — trust is computed from grounding +
  lifecycle; Satorid just sets `grounding` honestly.
- **Anchors, not copies** — bodies stay in Satorid behind `hydration_ref`.

Reference docs: `docs/integrations/external_data_schemas.md`,
`docs/confidence_class.md`, `docs/approval_workflow.md`,
`docs/contracts/external_evidence_contract.md`.

---

## 10. Build checklist for Codex

1. **Webhook/poll receiver** per connector → normalize provider payload to a
   neutral event object. Persist raw event in Satorid (you are the hydration
   store).
2. **Admission rules** per connector (the `SourceEventRule` pattern): match
   event kind, decline noise, honor user config. Output an external-evidence
   payload or nothing.
3. **Payload builder** → set the correct schema (`data_type_flag`), the identity
   fields (§4), `hydration_ref` back into Satorid, `core_memory_unifying_id`,
   and `grounding` only when parsing/deriving.
4. **HTTP client** → POST to the matching endpoint with auth header; branch on
   receipt `status` (§3); persist the returned `bead_id` keyed by your event.
5. **Idempotency** → use the provider delivery id as `source_event_id`; trust
   Core Memory to dedupe. Track `ingested_at` in Satorid to avoid re-polling.
6. **Approval wiring** → optionally set `approval_status: pending`; surface the
   pending queue and approve/reject in Satorid's UI.
7. **Config enforcement** → withhold categories/fields in the admission layer
   before any HTTP call.
8. **Tests** per connector: admitted event → correct bead type + receipt;
   withheld class → no call; re-delivery → `already_exists`; changed object →
   `version_superseded` (docs/records) or a second coexisting bead (operational).

Start with one connector end-to-end (HubSpot or GitHub) against the reference
implementation, then replicate the pattern.
