# Type Coverage Analysis — Document Adjustment & Structured Data

**Date:** 2026-06-12
**Scope:** All canonical type vocabularies in `core_memory/schema/` audited against the
situations introduced by document ingestion/adjustment and structured-data handling.
**Verdict:** The **bead type vocabulary is sufficient — no new `BeadType` members are
needed.** The gaps are one level down: three modifier vocabularies are missing or
non-canonical, the `Authority` enum is missing two values the ingest path already emits,
and the ingest path has no typed way to represent *change over time* in an external
source (the "adjust" half of the capability).

---

## 1. Vocabularies audited

| Vocabulary | Where | Size |
|---|---|---|
| `BeadType` / `CANONICAL_BEAD_TYPES` | `schema/models.py:44`, `schema/normalization.py:18` | 21 |
| `RelationshipType` / `CANONICAL_RELATION_TYPES` | `schema/models.py:101`, `schema/normalization.py:87` | 29 |
| `Status`, `Scope`, `Authority`, `ImpactLevel` | `schema/models.py` | 5 / 3 / 3 / 4 |
| `ClaimKind`, `ClaimUpdateDecision` | `schema/models.py:143,155` | 8 / 4 |
| Modifier sets (`HYPOTHESIS_STATUSES`, `OUTCOME_RESULTS`, `REVISION_TYPES`, `INCIDENT_SEVERITIES`, `TOOL_RESULT_STATUSES`, `TESTED_BY_VALUES`, `REFLECTION_TYPES`) | `schema/normalization.py:78-84` | — |
| External-family field vocabularies (`record_action`, `assertion_kind`, `document_kind`, `source_kind`, `data_type_flag`) | scattered — see §3 | — |

The external bead family (`transcript`, `document_reference`, `structured_observation`,
`state_assertion`, `data_insight`) plus typed fields on `Bead` (document/media family,
relational/structured family, interpreted/derived state family — `schema/models.py:703-742`)
covers every *first-contact* situation:

| Situation | Type | Covered |
|---|---|---|
| Register an external document or media object | `document_reference` | ✅ |
| Anchor an external conversation | `transcript` | ✅ |
| Observe a relational record / metric | `structured_observation` | ✅ |
| Analysis finding derived from data | `data_insight` | ✅ |
| Derived business state | `state_assertion` | ✅ |
| Agent performs a mutation (the act itself) | `tool_call` + `outcome` | ✅ |
| Document **adjusted** (new version of a known document) | — | ❌ see §2 |
| Structured record **updated** (new state of a known record) | — | ❌ see §2 |
| Document/record **deleted** at source (tombstone) | — | ❌ see §2 |

The relation vocabulary needs nothing new: version chains are expressible with
`supersedes`/`superseded_by` + `refines`; data-to-decision causality with `supports`,
`led_to`, `caused_by`, `derived_from`.

---

## 2. Core gap — change over time in an external source

`ingest_external_evidence` (`runtime/ingest/external_evidence.py:360`) dedups via
`_find_existing_external_bead` (`:201`) and returns `status: "already_exists"` with
**no update path**:

- A re-ingest matching `source_id` + `document_id` (an *adjusted document*) is dropped.
- A re-ingest matching `source_id` + `source_record_id` (an *updated record*) is dropped,
  even with a fresh `source_event_id`.

All HTTP surfaces (`/v1/memory/external-evidence`, `/v1/memory/document-reference`, etc.,
`integrations/http/server.py:514-570`) route through this function, so there is no typed
representation anywhere for "the document changed" or "the record changed."

The `Bead` schema already has every field needed — `supersedes`, `superseded_by`,
`effective_from`, `effective_to`, `record_action`, `status: superseded` — so this is not
a missing bead type. It is a missing **ingest behavior** plus the missing modifier
vocabularies in §3. The fix shape: on re-ingest with newer content, write a new bead of
the same type carrying `supersedes: [old_id]` and `record_action: "update"`, and mark the
prior bead `superseded_by`/`effective_to` — mirroring the claim layer's supersede
decision.

Deletion at source has no representation at all: no `record_action` value, nothing maps
to `status: archived`/`effective_to` from ingest.

---

## 3. Vocabulary-level gaps (concrete, small)

1. **`Authority` enum is missing two values the code already emits.**
   `external_evidence.py:334` writes `authority: "source_attributed"` (external anchors)
   and `"derived_analysis"` (state assertions). The enum (`schema/models.py:94`) has only
   `agent_inferred` / `user_confirmed` / `system`. The values survive only because
   `_normalize_choice(..., preserve_unknown=True)` passes unknown strings through —
   they are non-canonical in every stored external bead today.
   → Add `SOURCE_ATTRIBUTED = "source_attributed"` and `DERIVED_ANALYSIS = "derived_analysis"`.

2. **`record_action` has no canonical set.** It is the field that names the mutation —
   the heart of "adjust structured data" — yet it is a free string. Every comparable
   modifier (`hypothesis_status`, `result`, `severity`, `revision_type`, …) has a set in
   `normalization.py` and is normalized in `_normalize_bead_payload`.
   → Add `RECORD_ACTIONS = {"create", "update", "delete", "snapshot"}` and normalize like
   the others. `delete` doubles as the source-tombstone marker (§2).

3. **`assertion_kind` has no canonical set.** Default `"business_state"` is hardcoded in
   the ingest path (`external_evidence.py:266`), not in the schema layer.
   → Add `ASSERTION_KINDS = {"business_state", "document_claim", "entity_attribute", "metric_state"}`
   (seeded from the `STATE_ASSERTION_FLAGS` aliases already accepted at ingest).

4. **`data_type_flag` / `source_kind` vocabularies live in the wrong layer.** The flag
   sets (`TRANSCRIPT_FLAGS`, `DOCUMENT_FLAGS`, `RELATIONAL_FLAGS`, `STATE_ASSERTION_FLAGS`,
   `external_evidence.py:16-44`) are the de-facto canonical vocabulary for routing
   external payloads, but they live in `runtime/ingest/` while every other vocabulary
   lives in `schema/normalization.py`. Layering law says schema imports nothing, so the
   move is downward-safe.
   → Relocate the flag sets to `normalization.py`; `runtime/ingest` imports them.

5. **`REVISION_TYPES` is decision-shaped only** (`reversal`, `correction`). It does not
   model document revision. No change needed if §2 uses `record_action` + supersession
   for external sources — preferable to overloading `revision_type`, which belongs to
   agent-judgment beads.

6. **`ClaimKind` routes data-derived claims to `custom`.** Metric/attribute facts
   extracted from structured sources have no kind. Acceptable for now because
   `state_assertion` covers bead-level state; revisit only if claim-level resolution of
   business facts becomes a requirement (candidate: `measurement`).

7. **Promotion priors omit four of the five external types.** `BEAD_TYPE_PRIORS`
   (`policy/promotion.py:15`) lists `data_insight` but not `transcript`,
   `document_reference`, `structured_observation`, `state_assertion`. If anchors are
   intentionally non-promotable, encode that explicitly (prior `0.0` or an exclusion
   set) rather than falling through to default scoring.

---

## 4. Recommendation summary

No new `BeadType` members. In priority order:

1. Extend `Authority` with the two emitted values (today's data is non-canonical). — S
2. Add `RECORD_ACTIONS` + normalization; required before any update/delete flow. — S
3. Implement re-ingest supersession in `ingest_external_evidence` (replaces the silent
   `already_exists` drop for changed content). — M
4. Add `ASSERTION_KINDS`; move external flag vocabularies into `normalization.py`. — S
5. Make external-anchor promotion policy explicit. — S

---

## Addendum (2026-06-12) — implemented

Direction confirmed: no mutations — beads stay immutable; adjusted sources are
represented as version chains. Items implemented in this branch:

- **§2 (reframed):** re-ingest of an adjusted document/record writes a new
  version bead + `supersedes` chain; prior version closed (`status=superseded`,
  `effective_to`). See `docs/contracts/external_evidence_contract.md` →
  "Source version supersession".
- **Current-truth guard:** superseded versions excluded from the visible
  corpus; provenance callers opt in via `include_superseded`.
- **§3.1:** `Authority` extended with `source_attributed` / `derived_analysis`.
- **§3.3 + §3.4 (item 4):** `ASSERTION_KINDS` added; external flag
  vocabularies relocated to `schema/normalization.py`.
- **§3.7 (item 5):** explicit promotion priors + durability multipliers for
  all external types.
- **New:** confidence classes (C/B/A) as truth/governance status distinct
  from myelination, plus the `confirm_bead` user-confirmation surface
  (public API, store, HTTP). See `docs/confidence_class.md`.
- **§3.2 (`RECORD_ACTIONS`) intentionally not added:** the versioning model
  replaced the mutation-action framing; `record_action` remains a free-form
  source-side descriptor.
