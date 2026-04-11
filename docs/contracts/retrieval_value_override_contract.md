# Retrieval Value Override Contract

Status: canonical reviewed apply surface (DV2-3)

Purpose: allow reviewed Dreamer outputs to influence retrieval ranking through explicit, auditable override signals.

## Storage surface

Overrides are stored in canonical index projection:

- `.beads/index.json -> retrieval_value_overrides`

Audit events are appended to:

- `.beads/events/retrieval-value-overrides.jsonl`

## Override row shape

Each override row includes:

- `id`
- `source_bead_id`
- `target_bead_id`
- `relationship`
- `weight_delta` (bounded; additive on repeat applies)
- `status` (`active`)
- `reviewer`
- `notes`
- `source_proposal_id`
- timestamps + apply count

## Apply behavior

Reviewed accept of `retrieval_value_candidate` applies through this surface.

Effects:
- persists override row
- emits audit event
- canonical retrieval scoring can consume per-bead bonus derived from active overrides

## Guardrails

- no direct write bypass from Dreamer generation phase
- reviewed apply only
- bounded weight delta and additive saturation
- no destructive history rewrite
