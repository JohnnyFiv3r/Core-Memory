# Association Inference Contract v2.1 (Relation Taxonomy Guardrails)

## Scope

This contract applies to **new model-inferred association writes** (crawler/inference surface).

It does **not**:
- replace the repo-wide relationship vocabulary,
- require historical backfill,
- perform repo-wide enum cleanup.

Legacy rows remain readable for compatibility.

---

## Canonical temporal relationships

- Canonical temporal edge: `precedes`
- Semantics: `source precedes target` means source happened before target.
- Legacy `follows` inputs are accepted only as inverse-direction aliases and are
  rewritten by swapping endpoints to `precedes`.

---

## Canonical relationship set (inference surface)

The inference surface accepts the shared canonical relation vocabulary defined in
`core_memory/schema/normalization.py`. Common model-authored labels include:

- `causes`
- `leads_to`
- `supports`
- `supersedes`
- `refines`
- `blocks`
- `unblocks`
- `blocks_unblocks`
- `enables`
- `derived_from`
- `part_of`
- `precedes`
- `contradicts`
- `invalidates`
- `diagnoses`
- `resolves`

Anything else is non-canonical for this inference surface.

---

## Relation direction and disambiguation

The association payload stores exactly the submitted `source_bead`,
`target_bead`, and normalized canonical `relationship`. Normalization fixes label
spelling only; it never rewrites source/target direction.

- `causes`: source is evidence/cause for the affected target.
- `leads_to`: process/progression edge, not generic causal proof.
- `blocks`: source prevents or blocks target.
- `unblocks`: source removes a blocking condition for target.
- `blocks_unblocks`: legacy compound relation, accepted but discouraged for new
  model-authored associations unless the transition itself is being represented.
- `supports`: source meaningfully supports target. Do not use it as a fallback
  for unknown semantics.

Accepted aliases such as `caused_by`, `led_to`, `unblocked`, `enabled`,
`conflicts_with`, `related_to`, `reinforces`, `mirrors`,
`structural_symmetry`, `solves_same_mechanism`, `transferable_lesson`,
`violates_pattern_of`, and `blocks->unblocks` normalize to canonical relation
labels.

Inverse labels `blocked_by`, `superseded_by`, `follows`, and `specializes`
normalize to active canonical labels and swap source/target before validation.

Dreamer-specific pattern-family labels may appear as `relationship_signal`
metadata on candidates, but they are not canonical graph relation labels.

New model-authored writes should emit active labels directly when possible.

---

## Unknown / non-canonical relationship policy

- Do not map unknown to `supports`.
- Unknown/non-canonical relation handling:
  - strict mode: quarantine
  - permissive mode: `relationship="associated_with"` + `relationship_raw` + warning
- `associated_with` is non-structural by default for causal grounding.

---

## Model payload vs stored record

### Model payload (allowed)

- `source_bead`
- `target_bead`
- `relationship`
- `reason_text`
- `confidence`
- `provenance`
- optional `reason_code`
- optional `evidence_fields`
- optional `relationship_raw`

### System-assigned on storage

- `id`
- `type`
- `created_at`
- `normalization_applied`
- `warnings`

`relationship` is the single canonical stored relation field (no duplicate `normalized_relationship` field).

---

## Quarantine contract

Quarantined inferred edges are written to:

- `.beads/events/association-quarantine.jsonl`

They are:
- excluded from traversal
- excluded from canonical graph snapshot/index
- visible via diagnostics only

Recommended quarantine dedupe key:
- source
- target
- raw relationship
- reason_text
- provenance

---

## Compatibility bridge

- Accept legacy `rationale` at ingest.
- Canonical stored field is `reason_text`.
- If `rationale` present and `reason_text` missing:
  - set `reason_text = rationale`
  - warning `field_alias_applied:rationale->reason_text`

---

## Grounding rule for temporal edges

- `precedes` is traversable and can contribute context.
- `precedes`-only paths are not sufficient for `grounding=full`.
- `grounding=full` requires at least one non-temporal structural relation in the chain.
