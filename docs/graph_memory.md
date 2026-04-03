# Graph Memory Retrieval (R1-R4)

Core Memory retrieval uses three layers:

1. **Archive O(1) hydration**
   - `archive.jsonl` + `archive_index.json`
   - revision pointer -> byte offset seek/read
2. **Single graph model**
   - `events/edges.jsonl` event log
   - structural edges (immutable)
   - semantic edges (mutable via update/deactivate)
3. **Semantic + causal reasoning**
   - `bead_index_meta.json` (+ optional `bead_index.faiss`)
   - semantic lookup for anchors
   - structural-first causal traversal
   - semantic expansion only when structural grounding is insufficient

## CLI

```bash
core-memory --root ./memory graph build
core-memory --root ./memory graph stats
core-memory --root ./memory graph semantic-build
core-memory --root ./memory graph semantic-lookup --query "remember promotion" --k 8
core-memory --root ./memory graph traverse --anchor bead-123
core-memory --root ./memory graph decay
core-memory --root ./memory recall trace "why did we decide promotion must be candidate-only?" --k 8
core-memory --root ./memory memory execute --request '{"raw_query":"why promotion must be candidate-only?","intent":"causal","k":8}'
```

## Grounding rule

Reasoned answers are considered grounded when a returned chain includes:
- a `decision` or `precedent`, and
- at least one of `evidence`, `lesson`, or `outcome`.

If no grounded chain exists, the API returns a best-effort fallback message.
