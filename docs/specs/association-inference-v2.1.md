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

- Canonical temporal edges: `follows`, `precedes`
- Semantics: `source follows target` means source happened after target.
- Semantics: `source precedes target` means source happened before target.
- Traversal from a selected/current bead may walk `follows` edges into antecedent context.
- System must not auto-normalize `precedes` to `follows` or reverse source and target.

---

## Canonical relationship set (inference surface)

The inference surface accepts the shared canonical relation vocabulary defined in
`core_memory/schema/normalization.py`. Common model-authored labels include:

- `caused_by`
- `supports`
- `supersedes`
- `superseded_by`
- `refines`
- `blocked_by`
- `unblocks`
- `blocks_unblocks`
- `enables`
- `derived_from`
- `part_of`
- `follows`
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

- `caused_by`: source is explained by the target cause/mechanism in current
  stored Core Memory usage.
- `led_to`: process/progression edge, not generic causal proof.
- `blocked_by`: source is prevented by the target blocker in current stored
  Core Memory usage.
- `unblocks`: source removes a blocking condition for target.
- `blocks_unblocks`: legacy compound relation, accepted but discouraged for new
  model-authored associations unless the transition itself is being represented.
- `supports`: source meaningfully supports target. Do not use it as a fallback
  for unknown semantics.

Accepted aliases such as `causes`, `leads_to`, `blocks`, `unblocked`,
`enabled`, `conflicts_with`, `related_to`, and `blocks->unblocks` normalize to
the existing canonical relation labels without changing direction.

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

- `follows` is traversable and can contribute context.
- `follows`-only paths are not sufficient for `grounding=full`.
- `grounding=full` requires at least one non-temporal structural relation in the chain.
