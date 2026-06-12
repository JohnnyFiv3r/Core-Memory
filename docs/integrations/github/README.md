# GitHub Connector — Systems-of-Record Ingest Pattern

The GitHub connector is the reference implementation for ingesting events
from systems of record (GitHub, Jira, HubSpot, Linear, Salesforce, ...).
These systems contain data and documents and emit a high-volume event
stream — most of which does not warrant memory. The connector pattern is an
**admission layer**: map every event, write beads only for the ones that
matter.

```
provider webhook / poll
        │
        ▼
connector normalizes event           integrations/<system>/   (adapter)
        │
        ▼
ingest_source_event(...)             runtime/ingest/source_events.py
  rule set: bead-worthy?
        │ no → skip receipt (with reason)
        ▼ yes
ingest_external_evidence(...)        typed anchors, idempotent, versioned
```

## Layers

| Layer | Where | Owns |
|---|---|---|
| Connector (adapter) | `integrations/github/connector.py` | provider payload shapes, default admission rules |
| Generic engine | `runtime/ingest/source_events.py` | rule matching, skip receipts, fan-out to external evidence |
| External evidence | `runtime/ingest/external_evidence.py` | typed bead writes, idempotency, source version supersession |

Connectors consume only the `core_memory` public API
(`ingest_source_event`, `SourceEventMapping`, `SourceEventRule`) — like
every other adapter, no privileged access.

## Bead-worthiness

Admission is **declarative event-level policy**, not agent judgment.
Associations between the resulting beads and the rest of memory remain
agent-judged at `agent_end` — the invariant is untouched. A rule can decline
in two ways: the event kind never matches (`event_not_bead_worthy`), or the
builder inspects the payload and returns `None` (`rule_declined:<rule>` —
e.g. a PR closed without merging, a bot actor, a push to a feature branch).

Default GitHub policy:

| Event | Decision | Bead |
|---|---|---|
| `pull_request.closed` (merged) | record of change | `structured_observation`, `record_action: merged` |
| `pull_request.closed` (unmerged), opens, syncs | noise | skipped |
| `issues.closed` | outcome record | `structured_observation`, `record_action: closed` |
| `issues.opened` / labeled / assigned | workflow noise | skipped |
| `release.published` | document of record | `document_reference`, `document_kind: release_notes` |
| `push` to default branch touching docs (`.md`/`.mdx`/`.rst`/`.txt`) | document adjusted | one `document_reference` per file, **versioned** |
| `push` elsewhere, stars, comments, bots | noise | skipped |

## Versioning falls out for free

Because the connector routes through `ingest_external_evidence`, document
adjustments at the source become supersession chains automatically: a second
push that touches `docs/runbook.md` writes a new version bead, closes the
prior one (`status: superseded`, `effective_to`), and links them with a
`supersedes` association. Retrieval returns the current version only;
provenance callers pass `include_superseded` to walk the chain. Webhook
re-delivery (same `X-GitHub-Delivery`) is idempotent.

## Surfaces

- **Python:** `core_memory.integrations.github.ingest_github_event(root, event_name=..., event=..., delivery_id=...)`
- **HTTP:** `POST /v1/ingest/github` — reads `X-GitHub-Event` and
  `X-GitHub-Delivery` headers, body is the raw webhook JSON. Skipped events
  return `{"status": "skipped", "reason": ...}` with HTTP 200 (the webhook
  was handled; it just didn't warrant memory).

## Writing a connector for another system

1. Decide which event kinds are records of change vs. workflow noise. The
   bar: would an agent ever cite this event as evidence, context, or cause?
2. Write one builder per admitted kind that produces an external-evidence
   payload: stable `source_event_id` (provider delivery/event id), stable
   object identity (`source_record_id` or `document_id`) so adjustments
   version instead of duplicating, `hydration_ref` pointing back at the
   system of record, and `core_memory_unifying_id` for cross-store joins.
3. Register the rules in a `SourceEventMapping(source_system=...)` and call
   `ingest_source_event` per event.

Sketches: Jira — `jira:issue_updated` with a status transition to a terminal
state → `structured_observation` (`record_action: resolved`); description
edits on tracked epics → versioned `document_reference`; comment churn →
skipped. HubSpot — deal stage change to closed-won/lost →
`structured_observation`; note/attachment uploads → `document_reference`;
property recalculations → skipped.
