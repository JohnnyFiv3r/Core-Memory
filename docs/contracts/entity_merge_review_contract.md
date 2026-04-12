# Entity Merge Review Contract

Status: canonical reviewed flow (ER-2)

Purpose: provide a reviewable, auditable merge path for likely entity aliases without destructive history rewrite.

## Proposal shape

Entity merge proposals are stored in canonical index projection:

- `.beads/index.json -> entity_merge_proposals`

Each proposal row includes:

- `id`
- `kind` (`entity_merge`)
- `left_entity_id`
- `right_entity_id`
- `score`
- `reasons` (heuristic evidence)
- `status` (`pending|accepted|rejected`)
- `source` (`heuristic` / future `dreamer`)
- timestamps (`created_at`, `updated_at`, optional review fields)

## Suggest path

`suggest_entity_merge_proposals(...)` emits pending proposals from deterministic similarity heuristics over canonical entities/aliases.

ER-2 constraints:
- no auto-merge by default
- proposals are reviewable before apply

## Decision path

`decide_entity_merge_proposal(...)` supports:

- `decision=accept` (optionally `apply=true`)
- `decision=reject`

Accepted + apply behavior:
- merge aliases/provenance/confidence into kept entity
- mark merged entity `status=merged`, `merged_into=<keep_id>`
- remap alias map to kept entity
- rewrite bead `entity_ids` references from merged id to kept id (deduped)

## Auditability

- proposal rows retain review fields (`reviewer`, notes, timestamps)
- accepted/rejected outcomes remain persisted
- rejected proposals remain for calibration and analysis

## Non-goals

- aggressive auto-collapse of entities
- ontology expansion
- destructive rewrite of historical bead semantic content
