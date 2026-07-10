# Chunk turn ingest contract

Contract: `memory.chunk_turns.v1`

Core Memory stores owned-ingestion L2 chunks as native turn records so section
beads can cite chunk IDs in `source_turn_ids` and use the existing hydration and
adjacency path. The host does not maintain a second L2 copy.

## Write

`POST /v1/memory/chunk-turns`

```json
{
  "records": [
    {
      "schema": "chunk_turn_record.v1",
      "workspace_id": "workspace-id",
      "source_document_id": "document-id",
      "section_id": "section-id",
      "chunk_id": "deterministic-chunk-id",
      "chunk_index": 0,
      "content_text": "Full chunk text",
      "content_hash": "sha256:...",
      "source_element_ids": ["element-id"],
      "chunk_set_version": 1,
      "hydration_ref": {
        "schema": "hydration_ref.v2",
        "version": 2,
        "kind": "chunk_turn",
        "source": {
          "workspace_id": "workspace-id",
          "source_document_id": "document-id"
        },
        "target": {
          "chunk_turn_id": "deterministic-chunk-id",
          "core_memory_unifying_id": "raw-source-object-id",
          "chunk_set_version": 1
        }
      },
      "metadata": {}
    }
  ]
}
```

The complete batch is validated before writes. Repeating the same immutable
chunk record returns `already_exists`; reusing a `chunk_id` for different
content, ownership, position, version, or hydration metadata fails closed.
Records are grouped by document section and chunk-set version, then archived in
`chunk_index` order so native adjacent-turn hydration preserves document order.

## Read and GC planning

`GET /v1/memory/chunk-turns?core_memory_unifying_id=...&chunk_set_version_lte=...`

The read surface returns matching chunk IDs and version metadata without chunk
text. It supports version-aware inspection and GC planning; destructive GC is a
separate governed operation.

## Boundaries

- `hydration_ref.v2` and `target.core_memory_unifying_id` are required.
- Chunk records are hydration/evidence units, not causal bead units.
- Chunk evidence-vector indexing and resolve-up into the parent section bead
  are a separate retrieval slice.
