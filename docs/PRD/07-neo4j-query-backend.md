# PRD: Neo4j Query Backend

**Phase:** 7
**Status:** Not started
**Prerequisite:** Phase 6 complete (`BackendCapabilities` + extended `StorageBackend` protocol)

---

## Problem

The Neo4j integration (`integrations/neo4j/`) currently works as a **one-way sync target**:
`sync_to_neo4j()` pushes beads and associations into Neo4j as a mirror. Neo4j cannot serve
reads back to the recall pipeline — it is a visualization/export tool, not a backend.

This means causal traversal still runs in Python against the flat association list, even
when Neo4j is connected and could execute multi-hop graph queries natively and more
efficiently. The two components (`sync.py` writes, retrieval pipeline reads) have no
connection.

From the storage adapter review: "Core Memory supplies the causal ontology and traversal
policy. Neo4j executes graph operations efficiently." Phase 7 closes that gap.

---

## What exists

| Component | Status | Notes |
|-----------|--------|-------|
| `integrations/neo4j/client.py` | Done | `Neo4jClient` — driver, upsert, prune |
| `integrations/neo4j/mapper.py` | Done | `bead_to_node`, `association_to_edge` |
| `integrations/neo4j/sync.py` | Done | Write path: `sync_to_neo4j`, `neo4j_status` |
| `integrations/neo4j/config.py` | Done | `Neo4jConfig`, env var loading |
| `integrations/neo4j/backend.py` | **Missing** | Read path implementing `StorageBackend` |
| `traverse()` via Cypher | **Missing** | |
| `search_candidates()` via Neo4j vector index | **Missing** | Optional; see scope |
| `create_backend(root, backend="neo4j")` | **Missing** | Factory registration |

---

## Success criteria

1. `create_backend(root, backend="neo4j")` returns a `Neo4jBackend` instance.
2. `Neo4jBackend.capabilities().graph_traversal` is `True`.
3. `traverse(seed_ids, edge_types, max_hops)` executes a Cypher variable-length path
   query and returns bead dicts that match the `StorageBackend` contract.
4. `get_bead`, `put_bead`, `query_beads`, `get_associations`, `get_associations_for_bead`,
   `get_stats` all work (delegating to the local `JsonFileBackend` or `SqliteBackend` for
   persistence; Neo4j serves traversal queries only — see architecture note below).
5. The sync write path (`sync.py`) is unchanged. `Neo4jBackend` does not replace it.
6. Integration tests run against a local Neo4j instance (Docker Compose in `tests/`) and
   cover at minimum: 1-hop traversal, 3-hop traversal, empty graph, disconnected seed.
7. When Neo4j is unreachable, `Neo4jBackend.capabilities()` returns `graph_traversal=False`
   and the Python fallback activates. No crash on connection failure.

---

## Architecture note: hybrid storage model

`Neo4jBackend` does not replace local persistence. The recommended production setup is:

```
Local storage (json or sqlite) → source of truth for bead writes and projection cache
Neo4j                          → mirror (via sync.py) + graph traversal reads
```

`Neo4jBackend` wraps a local `StorageBackend` for all non-traversal operations and
delegates `traverse()` to Neo4j. This keeps local storage as the source of truth and Neo4j
as the traversal accelerator.

```python
class Neo4jBackend:
    def __init__(self, local: StorageBackend, config: Neo4jConfig):
        self._local = local
        self._neo4j = Neo4jClient(config)

    def capabilities(self) -> BackendCapabilities:
        reachable = self._probe_neo4j()
        return BackendCapabilities(graph_traversal=reachable)

    # All local-storage methods delegate to self._local
    def get_bead(self, bead_id): return self._local.get_bead(bead_id)
    # ...

    def traverse(self, seed_ids, edge_types, max_hops):
        # Cypher execution via self._neo4j
        ...
```

---

## Scope

**In:**
- `integrations/neo4j/backend.py` — `Neo4jBackend` class
- `traverse()` via Cypher variable-length paths
- Graceful degradation when Neo4j unreachable (capability flag → False)
- `create_backend` factory: add `"neo4j"` case
- Integration tests with Docker Compose Neo4j

**Out (Phase 7):**
- `search_candidates()` via Neo4j vector indexes. Neo4j vector index support exists but
  is optional and additive. Declare `vector_search=False` for now; add in a follow-on.
- Changes to `sync.py` write path
- Multi-database Neo4j support
- Neo4j authentication config changes (use existing `Neo4jConfig`)

---

## Key Cypher queries

### Variable-length traversal

```cypher
MATCH path = (start:Bead {id: $seed_id})-[r*1..$max_hops]->(n:Bead)
WHERE ($edge_types IS NULL OR type(r[-1]) IN $edge_types)
RETURN n, [rel in relationships(path) | type(rel)] AS edge_path
LIMIT 200
```

This mirrors what `graph/traversal.py:_traverse_from_seed` does in Python against the
flat association list. The result shape must be compatible: a list of bead dicts with
an `_edge_path` metadata key for inspection.

### Reachability check (for `capabilities()` probe)

```cypher
RETURN 1
```

Time-bounded (100ms timeout). If it fails, `graph_traversal=False`.

---

## Integration test setup

Add `tests/docker-compose.neo4j.yml`:

```yaml
services:
  neo4j-test:
    image: neo4j:5
    environment:
      NEO4J_AUTH: neo4j/testpassword
    ports:
      - "7687:7687"
    tmpfs:
      - /data
```

Tests that require Neo4j are marked `@pytest.mark.neo4j` and skipped unless
`CORE_MEMORY_NEO4J_URI` is set or the Docker Compose service is up.
