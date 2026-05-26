# Execution Plan: Phase 7b — Qdrant + Kuzu Migration

**Phase:** 7b execution
**Status:** Ready to start
**Design reference:** `docs/PRD/07b-qdrant-kuzu-migration.md` (rationale, schemas, config)
**Protocol reference:** `docs/PRD/07-neo4j-query-backend.md` (`GraphBackend` protocol)
**Prerequisite:** Phase 6 complete (`BackendCapabilities` wired, `_caps` branches in
`canonical.py` exist but unreachable)

This document is the **task-by-task execution plan**. It does not re-argue the
design — see the design PRD for that. Every step lists the files touched, the
exact change, the acceptance gate, and the rollback path.

---

## Storage tier model after this phase

Four tiers, each with one job:

```
.turns/session-{id}.jsonl + .idx.json   transcript hydration (byte-offset index)
.beads/sessions/session-*.jsonl         bead write log (source of truth)
.beads/qdrant/                          vector + sparse retrieval (Qdrant embedded)
.beads/kuzu/                            causal graph traversal (Kuzu embedded)
```

`.beads/index.json` (and the SQLite projection) **stay during this phase** as a
read model for `hydrate_bead_sources` and incident/tag lookups. They are flagged
for consolidation in Phase 7c (stretch — see "Out of scope" below).

---

## Acceptance criteria for the phase

Phase 7b ships when **all** of these are green:

1. Default `CORE_MEMORY_VECTOR_BACKEND=qdrant` (embedded), no FAISS files written
   on a fresh install.
2. Default `CORE_MEMORY_GRAPH_BACKEND=kuzu` (embedded), `.beads/kuzu/` created on
   first bead write.
3. `hybrid_lookup` issues one Qdrant hybrid query when backend is Qdrant; falls
   back to the existing `semantic_lookup` + `lexical_lookup` merge only when
   backend is `local-faiss`.
4. `canonical.py` `_caps.vector_search` branch reachable and exercised in tests.
5. `canonical.py` `_caps.graph_traversal` branch reachable and exercised in tests.
6. `core-memory migrate` populates Qdrant + Kuzu from an existing JSON store and
   is idempotent.
7. The 8 E2E tests in `tests/test_retrieval_e2e_qdrant_kuzu.py` pass with no
   mocks.
8. Regression suite passes (no new failures beyond the 14 pre-existing).
9. `CORE_MEMORY_GRAPH_BACKEND=neo4j` runs the same `traverse()` Cypher unchanged
   against a Neo4j instance.

---

## Task sequence

The tasks split into two independent tracks (Qdrant 7b-1..3, Kuzu 7b-4..5) plus
shared work (7b-6..9). The two tracks can land in either order; nothing in the
Qdrant track touches the graph backend and vice versa.

```
7b-1 ──► 7b-2 ──► 7b-3 ─┐
                        ├─► 7b-6 ─► 7b-7 ─► 7b-8 ─► 7b-9
7b-4 ──► 7b-5 ──────────┘
```

---

### 7b-1 — Embedded Qdrant + flip default

**Files**
- `core_memory/retrieval/semantic_index.py`
- `core_memory/retrieval/vector_backend.py` (verify embedded path support)
- `pyproject.toml` (add `qdrant-client[fastembed]` to default deps)

**Change**

In `semantic_index.py:35` — flip the default branch in `_normalize_vector_backend`:

```python
def _normalize_vector_backend(value: str | None) -> str:
    v = str(value or VECTOR_BACKEND_QDRANT).strip().lower().replace("_", "-")
    if v in {"", "auto", "qdrant"}:
        return VECTOR_BACKEND_QDRANT
    if v in {"local", "faiss", "local-faiss"}:
        return VECTOR_BACKEND_LOCAL_FAISS
    ...
```

In `semantic_index.py:61` — add embedded path in `_create_external_backend`:

```python
if backend == VECTOR_BACKEND_QDRANT:
    url = os.environ.get("CORE_MEMORY_QDRANT_URL")
    path = os.environ.get("CORE_MEMORY_QDRANT_PATH") or str(root / ".beads" / "qdrant")
    return create_vector_backend(
        "qdrant",
        collection_name=collection,
        url=url,                     # None → embedded
        path=None if url else path,
        dimensions=int(max(1, dimension)),
    )
```

In `vector_backend.py` — accept `path=` for `"qdrant"`, instantiate
`QdrantClient(path=path)` when `url` is None.

**Acceptance**
- Unit: `tests/test_qdrant_embedded_backend.py` creates a backend in a tmp dir,
  upserts 5 points with payloads, retrieves them by id, runs a filtered query
  (`retrieval_eligible=true`) and asserts retracted beads are excluded.
- `CORE_MEMORY_VECTOR_BACKEND` unset → backend resolves to `qdrant`.
- `CORE_MEMORY_VECTOR_BACKEND=local-faiss` → FAISS path still works (regression).

**Rollback**: set `CORE_MEMORY_VECTOR_BACKEND=local-faiss` in env.

---

### 7b-2 — Qdrant hybrid query in `hybrid_lookup`

**Files**
- `core_memory/retrieval/hybrid.py`
- `core_memory/retrieval/vector_backend.py` (add `hybrid_query` method)

**Change**

Add `hybrid_query(query: str, k: int, must: list[Filter]) -> list[dict]` to the
Qdrant vector backend that runs Qdrant's native sparse+dense fusion (FastEmbed
sparse vectors) in one call.

In `hybrid.py:27`, branch on backend:

```python
def hybrid_lookup(root: Path, query: str, k: int = 8, w_sem: float = 0.55, w_lex: float = 0.45) -> dict:
    anchor_meta = resolve_query_anchors(query, root)
    retrieval_query = str(anchor_meta.get("expanded_query") or query or "").strip() or str(query or "")

    backend = _configured_vector_backend()
    if backend == VECTOR_BACKEND_QDRANT:
        rows = _qdrant_hybrid(root, retrieval_query, k=max(10, int(k) * 3))
        return _finalize_hybrid(rows, root, query, retrieval_query, anchor_meta,
                                weights={"hybrid": 1.0}, normalization={"hybrid": "qdrant_native"})

    # Existing FAISS + lexical merge path stays as fallback
    sem = semantic_lookup(root, query=retrieval_query, k=max(10, int(k) * 3))
    lex = lexical_lookup(root, query=retrieval_query, k=max(10, int(k) * 3))
    ...
```

`_finalize_hybrid` is the existing post-merge logic (incident boost, topic boost,
sort, rank) extracted as a helper so both paths share it.

**Filters pushed to Qdrant:**
```python
must = [
    FieldCondition(key="retrieval_eligible", match=MatchValue(value=True)),
    FieldCondition(key="status", match=MatchValue(value="active")),
]
```

**Acceptance**
- Unit: `tests/test_hybrid_lookup_qdrant.py` — same input across FAISS and
  Qdrant paths returns same top-k bead_ids (allowing rank shuffle within ties).
- Retracted beads never appear in Qdrant path results.
- `lexical.py` is not imported when backend is Qdrant.

**Rollback**: FAISS branch still present and selected by env var.

---

### 7b-3 — Fill `_caps.vector_search` branch in `canonical.py`

**Files**
- `core_memory/retrieval/pipeline/canonical.py`

**Change**

The branch added in Phase 6 currently looks like:

```python
if _caps.vector_search:
    raise NotImplementedError("vector_search capability not yet wired")
else:
    sem = semantic_lookup(...)
```

Replace with:

```python
if _caps.vector_search:
    # Backend's own search_candidates() returns pre-filtered, pre-fused results
    sem_rows = store.search_candidates(
        query=retrieval_query,
        k=max(10, int(sem_k) * 3),
        filters={"retrieval_eligible": True, "status": "active"},
    )
    sem = {"ok": True, "results": sem_rows, "backend": "qdrant"}
else:
    sem = semantic_lookup(root, query=retrieval_query, k=max(10, int(sem_k) * 3))
```

This means `_caps.vector_search` is `True` when the configured storage backend
implements `search_candidates`. For JSON/SQLite storage with Qdrant alongside,
`get_backend_capabilities` returns `vector_search=True` when
`CORE_MEMORY_VECTOR_BACKEND=qdrant`.

**Update `get_backend_capabilities`** in `persistence/backend.py` to read the
vector backend env var:

```python
def get_backend_capabilities(beads_dir: Path) -> BackendCapabilities:
    backend = os.environ.get("CORE_MEMORY_BACKEND", "json").lower()
    vector = os.environ.get("CORE_MEMORY_VECTOR_BACKEND", "qdrant").lower()
    graph = os.environ.get("CORE_MEMORY_GRAPH_BACKEND", "kuzu").lower()
    return BackendCapabilities(
        vector_search=(vector == "qdrant"),
        graph_traversal=(graph in ("kuzu", "neo4j")),
        full_text_search=(vector == "qdrant"),
        transcript_hydration=False,
    )
```

**Acceptance**
- `tests/test_backend_capabilities.py` — extend to cover vector/graph env var
  combinations.
- `tests/test_canonical_hydration_contract.py` passes under
  `CORE_MEMORY_VECTOR_BACKEND=qdrant`.

**Rollback**: unset env var → caps go back to all-False → original branches run.

---

### 7b-4 — `KuzuGraphBackend`

**Files (new)**
- `core_memory/persistence/graph/__init__.py`
- `core_memory/persistence/graph/kuzu_backend.py`
- `core_memory/persistence/graph/factory.py`

**Files (modify)**
- `pyproject.toml` — add `kuzu` to default deps

**Change**

Implement `KuzuGraphBackend` against the `GraphBackend` protocol from PRD 07.
Methods to ship in this step:

```python
class KuzuGraphBackend:
    name = "kuzu"

    def __init__(self, path: Path):
        import kuzu
        self._db = kuzu.Database(str(path))
        self._conn = kuzu.Connection(self._db)
        self._ensure_schema()

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(graph_traversal=True)

    def health(self) -> dict: ...
    def close(self) -> None: ...

    def on_bead_written(self, bead: dict) -> None:
        self._conn.execute(
            "MERGE (b:Bead {id: $id}) "
            "SET b.type=$type, b.title=$title, b.session_id=$session_id, "
            "    b.created_at=$created_at, b.status=$status",
            {"id": bead["id"], "type": bead.get("type", ""),
             "title": bead.get("title", ""), "session_id": bead.get("session_id", ""),
             "created_at": bead.get("created_at", ""), "status": bead.get("status", "active")},
        )

    def on_association_written(self, assoc: dict) -> None:
        self._conn.execute(
            "MATCH (s:Bead {id: $src}), (t:Bead {id: $tgt}) "
            "MERGE (s)-[r:Association {rel_type: $rel_type}]->(t) "
            "SET r.confidence=$confidence, r.created_at=$created_at",
            {"src": assoc["source_bead_id"], "tgt": assoc["target_bead_id"],
             "rel_type": assoc["relation"], "confidence": float(assoc.get("confidence", 1.0)),
             "created_at": assoc.get("created_at", "")},
        )

    def on_bead_retracted(self, bead_id: str) -> None:
        self._conn.execute(
            "MATCH (b:Bead {id: $id}) SET b.status='retracted'", {"id": bead_id}
        )

    def traverse(self, seed_ids: list[str], edge_types: list[str] | None,
                 max_hops: int, max_chains: int = 16) -> list[dict]:
        # Cypher query from design PRD §"Traversal query"
        ...
```

Schema bootstrapping in `_ensure_schema()` is idempotent — wrap `CREATE NODE
TABLE` / `CREATE REL TABLE` in try/except for "already exists".

**Factory** in `factory.py`:

```python
def create_graph_backend(root: Path) -> GraphBackend:
    name = os.environ.get("CORE_MEMORY_GRAPH_BACKEND", "kuzu").lower()
    if name == "kuzu":
        path = Path(os.environ.get("CORE_MEMORY_KUZU_PATH") or (root / ".beads" / "kuzu"))
        return KuzuGraphBackend(path)
    if name == "neo4j":
        from .neo4j_backend import Neo4jGraphBackend
        return Neo4jGraphBackend.from_env()
    if name == "none":
        return NullGraphBackend()
    raise ValueError(f"unknown_graph_backend:{name}")
```

**Acceptance**
- `tests/test_kuzu_graph_backend.py` — temp dir Kuzu DB, schema bootstrap, bead
  merge idempotent, association merge idempotent, 1-hop / 3-hop traversal,
  edge-type filter, retracted node exclusion, empty graph returns `[]`.
- Re-opening a Kuzu DB at an existing path does not re-create schema.

**Rollback**: `CORE_MEMORY_GRAPH_BACKEND=none` → `NullGraphBackend` returns
empty traversal results, canonical falls back to Python walker.

---

### 7b-5 — Fill `_caps.graph_traversal` branch in `canonical.py`

**Files**
- `core_memory/retrieval/pipeline/canonical.py`

**Change**

The branch added in Phase 6 currently looks like:

```python
if _caps.graph_traversal:
    raise NotImplementedError("graph_traversal capability not yet wired")
else:
    trav = causal_traverse(...)
```

Replace with:

```python
if _caps.graph_traversal:
    graph = get_graph_backend(root)  # cached factory call
    chains = graph.traverse(
        seed_ids=[r["bead_id"] for r in seed_rows],
        edge_types=edge_types,
        max_hops=max_hops,
        max_chains=max_chains,
    )
    trav = {"chains": chains, "backend": graph.name}
else:
    trav = causal_traverse(root, seeds=seed_rows, edge_types=edge_types,
                           max_hops=max_hops, max_chains=max_chains)
```

Both branches must produce the same shape — verify in test 7b-4.

**Acceptance**
- `tests/test_canonical_trace_graph_backend.py` — same seed and edge filters
  produce same chain shape from Kuzu and from `causal_traverse_chains`.
- `trace_request` returns chains correctly when `CORE_MEMORY_GRAPH_BACKEND=kuzu`.

**Rollback**: `CORE_MEMORY_GRAPH_BACKEND=none` reverts to Python walker.

---

### 7b-6 — Write-path hooks

**Files**
- `core_memory/runtime/turn_processing.py` (or wherever `process_turn_finalized`
  performs `put_bead` and `put_association` — confirm exact location)

**Change**

After the existing local-storage write succeeds, mirror to vector + graph:

```python
store.put_bead(bead)

if bead.get("retrieval_eligible") and _vector_backend_is_qdrant():
    try:
        get_vector_backend(root).upsert(
            bead_id=bead["id"],
            text=_embed_text(bead),
            payload=_payload(bead),
        )
    except Exception as e:
        warnings.append(f"qdrant_upsert_failed:{bead['id']}:{e}")

try:
    get_graph_backend(root).on_bead_written(bead)
except Exception as e:
    warnings.append(f"graph_merge_failed:{bead['id']}:{e}")
```

And on association write:

```python
store.put_association(assoc)
try:
    get_graph_backend(root).on_association_written(assoc)
except Exception as e:
    warnings.append(f"graph_assoc_failed:{assoc['id']}:{e}")
```

`_embed_text(bead)` is `f"{title}. {summary}. {' '.join(retrieval_facts)}"`
(spec from design PRD §"Qdrant collection schema").

Hooks are **best-effort**. They surface warnings, never raise. This matches the
existing pattern for non-critical post-write operations.

**Acceptance**
- Writing a new bead with `CORE_MEMORY_VECTOR_BACKEND=qdrant` makes the bead
  queryable on the next `recall` call (no rebuild needed).
- Writing a new association with `CORE_MEMORY_GRAPH_BACKEND=kuzu` makes the
  edge traversable on the next `trace_request`.
- Killing the Qdrant directory mid-write logs a warning, does not raise.

**Rollback**: hook calls are gated on backend env var; disabling reverts to
zero-mirror behavior.

---

### 7b-7 — `core-memory migrate` CLI command

**Files (new)**
- `core_memory/cli_handlers_migrate.py`

**Files (modify)**
- `core_memory/cli.py` (or wherever subcommand dispatch lives) — register
  `migrate` subcommand

**Change**

```python
def handle_migrate(args) -> int:
    root = Path(args.root or ".").resolve()
    dry_run = bool(args.dry_run)
    skip_vectors = bool(args.skip_vectors)
    skip_graph = bool(args.skip_graph)

    store = create_backend(root / ".beads")
    beads = list(_iter_all_beads(store))     # from index.json or session jsonl
    assocs = list(_iter_all_associations(store))

    vec_count = 0
    if not skip_vectors and _vector_backend_is_qdrant():
        vec = get_vector_backend(root)
        for bead in beads:
            if not bead.get("retrieval_eligible"):
                continue
            if dry_run:
                vec_count += 1
                continue
            vec.upsert(bead_id=bead["id"], text=_embed_text(bead), payload=_payload(bead))
            vec_count += 1

    graph_node_count = graph_edge_count = 0
    if not skip_graph and _graph_backend_is_active():
        graph = get_graph_backend(root)
        for bead in beads:
            if not dry_run:
                graph.on_bead_written(bead)
            graph_node_count += 1
        for assoc in assocs:
            if not dry_run:
                graph.on_association_written(assoc)
            graph_edge_count += 1

    print(json.dumps({
        "dry_run": dry_run, "root": str(root),
        "vector": {"backend": _configured_vector_backend(), "upserted": vec_count},
        "graph":  {"backend": _configured_graph_backend(),
                   "nodes": graph_node_count, "edges": graph_edge_count},
    }, indent=2))
    return 0
```

CLI args: `--root`, `--dry-run`, `--skip-vectors`, `--skip-graph`.

**Acceptance**
- Run on an empty store: prints zero counts, exits 0.
- Run on a populated store: Qdrant collection count matches eligible bead count;
  Kuzu node count matches total bead count; edge count matches association count.
- Re-run: no duplicates (upsert/MERGE semantics); counts unchanged.
- `--dry-run` writes nothing but reports the same counts.

**Rollback**: command is opt-in; users who don't run it stay on FAISS until their
beads expire from FAISS naturally (no harm).

---

### 7b-8 — E2E tests

**Files (new)**
- `tests/test_retrieval_e2e_qdrant_kuzu.py`

**Setup** (per test module, shared fixture):
- Tmp dir with `CORE_MEMORY_VECTOR_BACKEND=qdrant` and
  `CORE_MEMORY_GRAPH_BACKEND=kuzu` in env.
- Write 20 beads across 3 sessions via real `process_turn_finalized` calls
  (no mocks). Beads:
  - One decision bead with 3 `caused_by` children
  - A 3-bead `supersedes` chain
  - A 5-bead `associated_with` cluster
  - 2 retracted beads
  - 3 beads with proper nouns in `retrieval_facts` (e.g., "Kuzu", "Qdrant",
    "FastEmbed") that don't appear in titles or summaries

**Test cases** (one per cell from the design PRD §"Test plan"):

1. `test_keyword_recall_proper_noun` — query "FastEmbed integration", assert
   the bead with "FastEmbed" only in `retrieval_facts` is in top-5.
2. `test_retracted_bead_excluded` — query that matches a retracted bead's
   summary; assert it is not in results.
3. `test_causal_chain_grounding_full` — anchor on decision bead; assert chains
   include all 3 children in `caused_by` direction; assert `grounding=="full"`.
4. `test_supersession_chain_ranks_latest_first` — query for superseded topic;
   assert the newest in chain ranks first.
5. `test_cross_session_recall` — bead from session 1 surfaces in a session 3
   query when semantically relevant.
6. `test_myelination_bonus_applied` — set artificial high bonus for one bead;
   assert it ranks above an equally-similar bead without bonus.
7. `test_rolling_window_independent_of_index` — bead written this turn appears
   in `build_visible_corpus` even if Qdrant upsert fails.
8. `test_migrate_idempotent` — run `core-memory migrate` twice; assert Qdrant
   collection count and Kuzu node count are identical after each run.

**Acceptance**: all 8 pass with no mocks, no network calls (OpenAI embedding
calls allowed but cached via existing fixture).

---

### 7b-9 — Neo4j as configurable alternative

**Files (new)**
- `core_memory/persistence/graph/neo4j_backend.py`

**Change**

`Neo4jGraphBackend` implements the same `GraphBackend` protocol. The `traverse()`
Cypher query is **identical** to the Kuzu version — only the driver instantiation
differs (uses `neo4j` Python driver from existing `integrations/neo4j/` code).

`from_env()` reads `CORE_MEMORY_NEO4J_URI`, `_USER`, `_PASSWORD`.

**Acceptance**
- `tests/test_graph_backend_neo4j_parity.py` (mocked driver) — running the
  Cypher against a mock that mimics Kuzu's result shape produces identical
  chain structures.
- Manual smoke (not CI): run E2E suite with `CORE_MEMORY_GRAPH_BACKEND=neo4j`
  against a local Neo4j; all graph tests pass.

**Rollback**: `CORE_MEMORY_GRAPH_BACKEND=kuzu` (default) bypasses Neo4j entirely.

---

## Out of scope (deferred)

These are intentionally **not** in 7b. They are tracked here so they aren't
forgotten and so the design discussion has a landing spot.

### 7c (stretch) — Eliminate `index.json` projection

Once Qdrant + Kuzu are stable and tested, evaluate replacing `index.json` reads:

- `hydrate_bead_sources` reads `index.json` to map `bead_id → source_turn_ids`.
  After 7b this could read from a Qdrant payload field instead, or from Kuzu
  via `MATCH (b:Bead {id: $id}) RETURN b.source_turn_ids`.
- `hybrid.py` reads `index.json` for `incident_id` and `tags` post-filter
  boosting. These could move to Qdrant payload and be filtered in-query.

This is a follow-up phase because:
- Risk: collapsing the projection cache while two new backends stabilize is too
  much change at once.
- Value: real, but small — saves one file read per hydration call.
- Coupling: requires schema additions to both Qdrant payload and Kuzu node
  table; better to know the access patterns first.

### Out of scope entirely
- Removing the session JSONL write log (source of truth, never touched)
- Removing `.turns/` transcripts (different retention + access pattern)
- Replacing the SQLite backend (orthogonal — SqliteBackend still works as the
  storage projection; Qdrant + Kuzu sit alongside)
- Multi-tenant Qdrant collections (one collection per root; not addressed here)

---

## Regression risk and rollback

Every step has a working rollback via env var:

| Failure mode | Rollback |
|---|---|
| Qdrant embedded breaks on a platform | `CORE_MEMORY_VECTOR_BACKEND=local-faiss` |
| Kuzu schema migration breaks | `CORE_MEMORY_GRAPH_BACKEND=none` → Python walker |
| Write-path hook stalls bead writes | Hooks are best-effort; gate via env var |
| `_caps` branch produces wrong results | Unset `CORE_MEMORY_VECTOR_BACKEND` / `_GRAPH_BACKEND` → caps go False → original code paths |

No step requires irreversible file changes. FAISS files are not deleted by the
migration. Session JSONL write log is untouched throughout.

---

## Dependencies added to `pyproject.toml`

```
qdrant-client[fastembed] >= 1.9
kuzu >= 0.4
```

Both are pure Python wheels with bundled native deps; no compile step on install.
Total install size delta: ~80 MB (FastEmbed model is the largest single piece).

---

## Sequencing checklist

```
[ ] 7b-1   Embedded Qdrant + default flip
[ ] 7b-2   hybrid_lookup Qdrant path
[ ] 7b-3   canonical.py _caps.vector_search branch
[ ] 7b-4   KuzuGraphBackend (schema, traverse, write hooks)
[ ] 7b-5   canonical.py _caps.graph_traversal branch
[ ] 7b-6   Write-path mirror hooks
[ ] 7b-7   core-memory migrate command
[ ] 7b-8   E2E test suite (8 cases)
[ ] 7b-9   Neo4j alternative backend
[ ] Regression suite green
[ ] Update CLAUDE.md "Active subsystems" — Qdrant + Kuzu as defaults
[ ] Update docs/architecture_overview.md retrieval section
```
