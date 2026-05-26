# PRD: Qdrant + Kuzu Retrieval Infrastructure Migration

**Phase:** 7b
**Status:** Not started
**Prerequisite:** Phase 6 complete (BackendCapabilities, `_caps` branches in canonical.py)
**Related:** Phase 7 (GraphBackend protocol) — this PRD is the concrete implementation,
7 is the abstract protocol design. Both ship together.

---

## Problem

Core Memory produces high-quality bead data per turn — structured causal types,
authored retrieval_facts, entity and topic tagging, because-chains. The retrieval
infrastructure does not match this data quality. The bottleneck is finding the right
beads, not generating them.

**FAISS** (current default vector store):
- No filtering at query time. `semantic_lookup` fetches top-k by cosine similarity,
  then Python filters out non-retrieval-eligible and retracted beads. If 10 of k=24
  candidates are ineligible, only 14 useful slots remain. The effective retrieval pool
  is smaller than configured.
- Dense-only. No BM25 component at this layer.
- Rebuild required. New beads are not searchable until a rebuild completes. Concurrent
  writes are unsafe (documented as single-writer only).
- File management. `index.faiss` + `rows.jsonl` + `manifest.json` are separate files
  that can drift if a write fails.

**`lexical.py`** (current hand-rolled BM25):
- `hybrid_lookup` calls `semantic_lookup` (FAISS) and `lexical_lookup` (lexical.py)
  separately, then combines them 55/45 in Python. Two index reads, two ranking passes,
  manual weight tuning.
- The tokenizer in `lexical.py` is a custom stopword list + counter. No stemming, no
  inverse document frequency, no positional weighting.

**`causal_traverse_chains`** (current Python graph walker):
- Walks the flat association list from `index.json` in Python. O(n) over all
  associations regardless of query scope.
- Edge-type filtering is post-traversal in Python, not in the traversal itself.
- Cannot express complex traversal patterns (e.g., "follow CAUSED_BY and LED_TO but
  skip ASSOCIATED_WITH, stop at retracted nodes") without custom code per pattern.

---

## What already exists

| Component | Status | Notes |
|-----------|--------|-------|
| `VECTOR_BACKEND_QDRANT` in semantic_index.py | Done | Server mode only; needs embedded path |
| `_create_external_backend("qdrant", ...)` | Done | Wires Qdrant client against URL |
| `_caps.vector_search` branch in canonical.py | Done (P6) | Unreachable today |
| `_caps.graph_traversal` branch in canonical.py | Done (P6) | Unreachable today |
| `GraphBackend` protocol | Planned (P7) | Ships with this PRD |
| `causal_traverse_chains` Python fallback | Done | Retained as fallback when Kuzu absent |
| `lexical_lookup` / `lexical.py` | Done | Deprecated by this PRD; stays as fallback |

---

## What changes

### Vector tier: Qdrant replaces FAISS + lexical.py

**Embedded mode (zero-ops default).** `qdrant-client` supports
`QdrantClient(path=".beads/qdrant")` — fully in-process, local directory, no server.
This replaces the `index.faiss` file with the same deployment story (just a directory).
Users who already run a Qdrant server set `CORE_MEMORY_QDRANT_URL` and skip embedded
mode. The existing server-mode wiring in `_create_external_backend` is unchanged.

**Hybrid search.** Qdrant's native sparse+dense query (using FastEmbed sparse vectors)
replaces the separate `semantic_lookup` + `lexical_lookup` combination in `hybrid.py`.
Instead of two queries merged in Python at 55/45, one Qdrant query returns pre-fused
results. `hybrid_lookup` becomes a thin wrapper over the Qdrant hybrid query.

**Filtering at query time.** The Qdrant query carries:
```python
must = [
    FieldCondition(key="retrieval_eligible", match=MatchValue(value=True)),
    FieldCondition(key="status", match=MatchValue(value="active")),
]
```
All k results returned are genuinely queryable. No post-fetch Python filtering needed
for eligibility or status. Optional filters (topic, type, session_id) can be pushed
down the same way.

**Real-time index.** Every `put_bead` call upserts to Qdrant immediately. No rebuild
required. New beads are searchable on the next query.

**Qdrant collection schema:**
```
Collection name: core_memory_{sha1(root)[:10]}   (existing _vector_collection_name logic)
Dense vector:    OpenAI text-embedding-* dimension (1536 or 3072, read from manifest)
Sparse vector:   FastEmbed sparse (for BM25 component of hybrid query)
Payload fields:
  bead_id:             str
  type:                str
  session_id:          str
  created_at:          str   (ISO-8601)
  retrieval_eligible:  bool
  status:              str   ("active" | "retracted" | "archived")
  topics:              list[str]
  entities:            list[str]
  title:               str
  promoted:            bool
```

Text used to generate the dense embedding:
`"{title}. {summary}. {' '.join(retrieval_facts)}"`
Same text used for sparse vector generation.

### Graph tier: Kuzu replaces Python causal_traverse_chains

**Embedded, zero-ops.** `kuzu` is a Python package — `import kuzu`, open a database
directory, run Cypher. No server, no Docker. Database lives at `.beads/kuzu/`.

**Kuzu schema:**
```
NODE TABLE Bead(
    id             STRING,
    type           STRING,
    title          STRING,
    session_id     STRING,
    created_at     STRING,
    status         STRING,
    PRIMARY KEY(id)
)

REL TABLE Association(
    FROM Bead TO Bead,
    rel_type       STRING,
    confidence     DOUBLE,
    created_at     STRING
)
```

One generic `Association` REL TABLE with `rel_type` property. The canonical relation
vocabulary (30+ types in `schema/normalization.py`) is large and extensible — typed
REL TABLEs would require schema migrations on new types. The `rel_type` field carries
the canonical string (`caused_by`, `supersedes`, `contradicts`, etc.).

**Traversal query:**
```cypher
MATCH path = (s:Bead)-[r:Association*1..$max_hops]->(n:Bead)
WHERE s.id IN $seed_ids
  AND n.status = 'active'
  AND ($edge_types IS NULL
       OR ALL(rel IN r WHERE rel.rel_type IN $edge_types))
WITH
    [node IN nodes(path) | {id: node.id, type: node.type, title: node.title}] AS nodes,
    [rel IN relationships(path) | {
        rel:  rel.rel_type,
        src:  startNode(rel).id,
        tgt:  endNode(rel).id
    }] AS edges,
    length(path) AS depth
ORDER BY depth ASC
LIMIT $max_chains
RETURN nodes, edges
```

Result shape is identical to `causal_traverse_chains` output. All downstream pipeline
code (grounding detection, chain filtering, hydration) is unchanged.

**Neo4j as alternative.** Same Cypher query, different driver instantiation.
`CORE_MEMORY_GRAPH_BACKEND=neo4j` uses `Neo4jClient` from the existing
`integrations/neo4j/` code. The query is identical; only the connection object differs.
This is wired through the `GraphBackend` protocol from Phase 7.

---

## What does NOT change

This is the more important list.

**Rolling window / `build_visible_corpus`.** Reads directly from session JSONL files.
Not touched by this migration. The in-session corpus tier is completely independent of
the vector index and graph database.

**Myelination.** `compute_myelination_bonus_map` reads from the myelination store
(separate from FAISS/Qdrant). Returns `bonus_by_bead_id`. Applied as a score additive
in Python after Qdrant returns candidates. The Qdrant payload could optionally carry
the myelination score in a future pass (enabling native Qdrant rescoring), but for this
PRD it is applied identically to today — post-fetch in `canonical.py`.

**Claims.** `_load_claim_state` and `resolve_all_current_state` are completely
separate from the vector index. Claim anchors are injected into the candidate set by
bead ID lookup from `by_id`, not by vector similarity query. Behavior is identical.

**Reranking, grounding, hydration.** `rerank_semantic_rows`, grounding level
detection, `hydrate_bead_sources` — all operate on the candidate list after retrieval.
None of these know or care whether candidates came from FAISS or Qdrant.

**Write pipeline.** `process_turn_finalized`, bead validation, association crawler,
claim extraction, promotion — all unchanged. The only addition is two calls after the
local storage write succeeds: `qdrant_upsert(bead)` and `kuzu_merge_bead(bead)`.

**Bead schema.** Unchanged.

**Session JSONL files.** Source of truth. Unchanged.

---

## Retrieval improvement mechanism

The improvements are concrete and specific, not speculative.

**Effective candidate pool.** With FAISS, if sem_k=24 and 10 candidates are
non-retrieval-eligible or retracted, the downstream pipeline works with 14 usable
candidates. With Qdrant filtering at query time, all 24 slots are eligible active
beads. The pipeline sees a meaningfully richer candidate set for the same k budget.

**Keyword recall.** The hand-rolled BM25 in `lexical.py` uses a custom stopword list
and token counter — no IDF, no stemming. Qdrant's sparse vectors (FastEmbed) use
proper TF-IDF weighting trained on a large corpus. Queries for proper nouns, specific
dates, technical terms, and model numbers — things that embed reasonably but tokenize
better — improve.

**Single-pass hybrid.** The current pipeline reads two indexes (FAISS + lexical) and
merges in Python. One Qdrant hybrid query returns a pre-fused result in one round-trip.
The simplification removes the manual weight tuning in `hybrid.py` (55/45) and the
separate `lexical.py` infrastructure.

**Graph traversal fidelity.** The Python walker stops at the association list boundary
— it cannot express "follow this edge type but not that one, and skip retracted nodes,
and cap at 5 chains." Each constraint is a separate Python filter pass. Kuzu's Cypher
expresses all constraints in one query and evaluates them during traversal, not after.
Deep chains (3+ hops in a large association graph) are significantly faster.

---

## Configuration

```
# Vector backend (default changes from local-faiss to qdrant)
CORE_MEMORY_VECTOR_BACKEND = qdrant | pgvector | local-faiss (deprecated)

# Qdrant — embedded mode (default, zero ops)
# Neither URL nor PATH set → embedded at .beads/qdrant/
CORE_MEMORY_QDRANT_PATH = /path/to/qdrant/dir   # override embedded location
CORE_MEMORY_QDRANT_URL  = http://localhost:6333  # use server instead of embedded

# Graph backend (default changes from none to kuzu)
CORE_MEMORY_GRAPH_BACKEND = kuzu | neo4j | none

# Kuzu — embedded mode (zero ops, default)
CORE_MEMORY_KUZU_PATH = /path/to/kuzu/dir        # default: .beads/kuzu/

# Neo4j — as alternative to Kuzu (existing config)
CORE_MEMORY_NEO4J_URI      = bolt://localhost:7687
CORE_MEMORY_NEO4J_USER     = neo4j
CORE_MEMORY_NEO4J_PASSWORD = ...
```

---

## Write path changes (minimal)

After the existing local storage write in `process_turn_finalized`:

```python
# Existing: write to JsonFileBackend / SqliteBackend
store.put_bead(bead)

# New: mirror to vector index and graph (best-effort, non-blocking)
if bead.get("retrieval_eligible"):
    vector_backend.upsert(bead_id=bead["id"], text=_embed_text(bead), payload=_payload(bead))

graph_backend.on_bead_written(bead)   # Kuzu MERGE or Neo4j MERGE
```

When an association is written:
```python
graph_backend.on_association_written(assoc)  # Kuzu REL merge or Neo4j REL merge
```

Both calls are best-effort: failures log a warning and surface in recall result
`warnings` list. They never block the local write. This matches the existing
pattern for all non-critical post-write operations.

---

## Migration command

Users with existing bead stores need to populate Qdrant and Kuzu on first run.

```
$ core-memory migrate --root=. [--dry-run] [--skip-vectors] [--skip-graph]
```

Steps:
1. Read all beads from `index.json` (or rebuild from session JSONL if index is absent).
2. For each retrieval-eligible, active bead: generate embedding (OpenAI, same provider
   as configured) and upsert to Qdrant.
3. For all beads: `MERGE (b:Bead {id: $id}) SET b += $props` in Kuzu.
4. For all associations: `MERGE` edge in Kuzu.
5. Report: beads indexed, associations merged, errors.

Idempotent. Safe to re-run. Existing Qdrant points are overwritten (upsert semantics).
Existing Kuzu nodes/edges are merged (MERGE semantics).

The command detects the configured backends and skips steps for unconfigured ones
(e.g., if `CORE_MEMORY_GRAPH_BACKEND=none`, skip step 3-4).

---

## Files that change

| File | Change type | Notes |
|------|-------------|-------|
| `retrieval/semantic_index.py` | Modify | Add embedded Qdrant path; change default from `local-faiss` to `qdrant` |
| `retrieval/hybrid.py` | Simplify | `hybrid_lookup` becomes Qdrant hybrid query when backend=qdrant; `lexical_lookup` is fallback only |
| `retrieval/lexical.py` | Deprecate | Retained as fallback; deprecation notice added |
| `retrieval/pipeline/canonical.py` | Fill branches | Fill `_caps.vector_search` and `_caps.graph_traversal` branches (currently unreachable stubs) |
| `persistence/graph/kuzu_backend.py` | New | `KuzuGraphBackend` implementing `GraphBackend` protocol |
| `persistence/graph/factory.py` | New | `create_graph_backend()`, registers kuzu + neo4j |
| `persistence/graph/__init__.py` | New | Package init |
| `runtime/write_path.py` (or equivalent) | Modify | Add post-write upsert to Qdrant + merge to Kuzu |
| `cli_handlers_migrate.py` | New | `core-memory migrate` command |
| `graph/traversal.py` | No change | Python walker retained as fallback |

---

## Test plan

### Unit tests (no external services)

- `tests/test_qdrant_embedded_backend.py` — embedded Qdrant in temp dir: collection
  creation, upsert with payload, filtered query (retrieval_eligible=true), hybrid query
  returns correct bead_id, retracted bead excluded from results.
- `tests/test_kuzu_graph_backend.py` — Kuzu in temp dir: schema creation, bead merge,
  association merge, 1-hop traversal, 3-hop traversal, edge-type filter, retracted node
  exclusion, empty graph returns empty chains.
- `tests/test_graph_backend_neo4j_query_parity.py` — given identical seed data,
  Kuzu query and Neo4j query (mocked) produce identical result shapes.

### End-to-end retrieval tests

`tests/test_retrieval_e2e_qdrant_kuzu.py` — full pipeline, no mocks:

**Setup:** write 20 beads across 3 sessions using `process_turn_finalized` with real
Qdrant embedded and real Kuzu. Beads cover: a decision with 3 causal children, a
supersession chain (3 beads), a cluster of associated beads, 2 retracted beads, 3
beads with specific proper nouns that embed poorly.

**Test cases:**

1. **Keyword recall**: query for a proper noun that appears verbatim in one bead's
   `retrieval_facts` but is not the semantic topic of the query. Assert that bead
   appears in top-5 (BM25 component fires). Fails with FAISS-only baseline.

2. **Eligibility filtering**: query that would semantically match a retracted bead.
   Assert retracted bead is not in results. Assert all returned beads have
   `retrieval_eligible=true`.

3. **Causal chain**: query anchored on the decision bead. Assert `trace_request`
   returns chains containing the 3 causal children in correct order. Assert
   `grounding == "full"`.

4. **Supersession chain**: query for the topic of the superseded bead. Assert the
   superseding bead ranks higher and the chain is correctly reconstructed.

5. **Cross-session recall**: bead from session 1 is relevant to a query in session 3.
   Assert it appears in results (not blocked by session scope).

6. **Myelination bonus applies**: a bead with high myelination score (artificially
   set) ranks above an equally-similar bead without. Assert score order reflects bonus.

7. **Rolling window independent**: in-session bead written this turn is in visible
   corpus regardless of Qdrant index state (upsert may not have completed). Assert
   bead visible via `build_visible_corpus` path.

8. **Migration idempotent**: run `migrate` twice, assert second run produces same
   Qdrant collection size (no duplicates) and same Kuzu node count.

### Regression suite

Run the full existing test suite with `CORE_MEMORY_VECTOR_BACKEND=qdrant` and
`CORE_MEMORY_GRAPH_BACKEND=kuzu` set. Assert no new failures beyond the 14 pre-existing
failures already tracked.

---

## Deprecation path

**FAISS:** remains functional behind `CORE_MEMORY_VECTOR_BACKEND=local-faiss`.
Emits a deprecation warning on startup: `"FAISS backend is deprecated; set
CORE_MEMORY_VECTOR_BACKEND=qdrant. FAISS will be removed in the next major version."`.
The `index.faiss` file is not deleted by migration.

**`lexical.py`:** retained as the BM25 path when vector backend is FAISS. When Qdrant
is the backend, `hybrid_lookup` routes to the Qdrant hybrid query and `lexical_lookup`
is not called. No deprecation warning on `lexical.py` itself — it silently becomes
unused in the Qdrant path.

---

## Implementation sequence

| Step | Work | Blocking? |
|------|------|-----------|
| 7b-1 | Embedded Qdrant path in `_create_external_backend`; change default | No |
| 7b-2 | `hybrid_lookup` Qdrant hybrid path; `lexical_lookup` as fallback | After 7b-1 |
| 7b-3 | Fill `_caps.vector_search` branch in `canonical.py` | After 7b-1 |
| 7b-4 | Kuzu schema + `KuzuGraphBackend.traverse()` + `on_bead_written` | No |
| 7b-5 | Fill `_caps.graph_traversal` branch in `canonical.py` | After 7b-4 |
| 7b-6 | Write-path hooks (Qdrant upsert + Kuzu merge on bead/assoc write) | After 7b-1, 7b-4 |
| 7b-7 | `core-memory migrate` CLI command | After 7b-1, 7b-4 |
| 7b-8 | E2E test suite | After 7b-6, 7b-7 |
| 7b-9 | Neo4j as configurable Kuzu alternative | After 7b-4 |

Steps 7b-1/7b-2/7b-3 (Qdrant) and 7b-4/7b-5 (Kuzu) are independent and can proceed
in parallel.
