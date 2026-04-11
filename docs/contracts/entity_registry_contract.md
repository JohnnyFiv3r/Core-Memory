# Entity Registry Contract

Status: canonical contract (ER-1)

Purpose: provide a narrow, canonical identity layer so equivalent labels can resolve to a shared entity id without introducing a parallel truth store.

## Storage model

Entity registry lives inside canonical index projection:

- `.beads/index.json -> entities`
- `.beads/index.json -> entity_aliases`

No second authority store is introduced.

### `entities` shape

`entities[entity_id]` object fields:

- `id`: canonical entity id (`entity-<hash>`)
- `label`: preferred human-readable label
- `normalized_label`: normalized label key
- `aliases`: normalized aliases for lookup
- `confidence`: confidence for identity quality (0..1)
- `provenance`: list of evidence rows (`kind`, `bead_id`, `source`, `ts`)
- `status`: lifecycle status (`active` for ER-1)
- `created_at`, `updated_at`

### `entity_aliases` shape

- maps `normalized_alias -> entity_id`
- used for fast deterministic alias resolution

## Normalization

Alias normalization is deterministic:

- lowercase
- punctuation/spacing normalized
- lightweight organization suffix stripping (`inc`, `llc`, etc.)

This is intentionally conservative in ER-1.

## Lifecycle (ER-1)

ER-1 lifecycle is narrow:

- create or reuse canonical entity id
- attach additional aliases/provenance
- link bead to resolved entity ids (`bead.entity_ids`)

Merge/reject proposal flow is deferred to ER-2.

## Integration behavior

- Bead writes with `entities` list should resolve to `entity_ids` during canonical add-bead path.
- Retrieval/Dreamer may use `entity_ids` as additional identity signal.
- Historical bead content is not rewritten destructively.

## Non-goals for ER-1

- giant ontology
- aggressive auto-merge
- replacing claim/state model
- introducing non-canonical storage planes
