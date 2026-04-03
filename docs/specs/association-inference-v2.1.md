# Association Inference Contract v2.1 (Temporal Policy Finalized)

## Scope

This contract applies to **new model-inferred association writes** (crawler/inference surface).

It does **not**:
- replace the repo-wide relationship vocabulary,
- require historical backfill,
- perform repo-wide enum cleanup.

Legacy rows remain readable for compatibility.

---

## Canonical temporal relationship

- Only canonical temporal edge: `follows`
- Semantics: `source follows target` means source happened after target.
- Traversal from a selected/current bead may walk `follows` edges into antecedent context.

### Non-canonical temporal label

- `precedes` is non-canonical for new inference.
- System must not auto-normalize `precedes` to `follows`.
- If `relationship="precedes"` appears in model-inferred payload:
  - strict mode: quarantine edge with warning `noncanonical_relationship:precedes`
  - permissive mode: map to `associated_with` (non-structural), preserve `relationship_raw="precedes"`

No silent direction rewrites.

---

## Canonical relationship set (inference surface)

- `caused_by`
- `supports`
- `supersedes`
- `blocked_by`
- `unblocks`
- `enables`
- `derived_from`
- `follows`
- `contradicts`

Anything else is non-canonical for this inference surface.

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
