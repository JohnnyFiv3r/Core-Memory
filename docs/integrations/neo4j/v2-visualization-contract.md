# Neo4j Visualization V2 Contract

Status: Proposed v2 (visualization-focused)

## Objective
Render Core Memory in Neo4j in a way that is immediately legible for humans:

1. **Node label = bead type** (e.g., `Decision`, `Outcome`, `Lesson`, `SessionStart`)
2. **Edge type = association relationship** (e.g., `SUPPORTS`, `PRECEDES`, `CONTRADICTS`)
3. **Rich metadata available on click** for nodes and edges

This v2 contract is for visualization ergonomics. It does **not** change canonical Core Memory write/retrieval authority.

---

## Authority and Safety (unchanged)

- Core Memory local state remains authoritative.
- Neo4j remains a shadow projection.
- Neo4j failures must not block canonical runtime paths.
- Dataset isolation is required:
  - `cm_owner=core_memory_shadow_v1`
  - `cm_dataset=<dataset-key>`
- Prune must remain scoped to owner+dataset.

---

## Node Contract (v2)

### Labels
- Primary visualization label is bead type label (`Decision`, `Outcome`, etc.).
- Optional compatibility label `Bead` may be retained for broad queries/tooling.

### Required properties
- `bead_id`
- `type`
- `title`
- `status`
- `session_id`
- `created_at`
- `cm_owner`
- `cm_dataset`

### Metadata visibility intent
Node click should expose rich properties (including summary/detail/tags/topics/entities/source_turn_ids and other mapped fields).

---

## Edge Contract (v2)

### Relationship type
- Neo4j relationship type is derived from `association.relationship`.
- Must be sanitized to valid Neo4j type tokens and normalized to uppercase.
  - Example: `supports` -> `SUPPORTS`
  - Example: `blocks_unblocks` -> `BLOCKS_UNBLOCKS`

### Required properties
- `association_id`
- `relationship` (original canonical value)
- `cm_owner`
- `cm_dataset`

### Noise control
- Visualization defaults should suppress high-noise relation classes (notably `shared_tag`) unless explicitly requested.

---

## Backward Compatibility

- Existing v1 projection can remain supported behind explicit mode selection.
- V2 should be opt-in initially (CLI/API flag), then can become default after validation.

---

## Acceptance criteria for v2 implementation

1. Graph render shows typed node labels (not only generic `Bead`).
2. Graph render shows typed relationships (not only `ASSOCIATED`).
3. Node metadata is preserved and inspectable in Neo4j Browser/Bloom.
4. `shared_tag` does not dominate default visualization view.
5. Owner+dataset prune safety guarantees remain intact.
6. Canonical Core Memory runtime behavior is unchanged.
