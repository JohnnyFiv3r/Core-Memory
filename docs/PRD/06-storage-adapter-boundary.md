# PRD: Storage Adapter Boundary — Capability Tiers

**Phase:** 6
**Status:** Not started
**Prerequisite:** Phase 5 complete

---

## Problem

Core Memory has two separate backend abstractions that do not know about each other:

- `StorageBackend` (Protocol in `persistence/backend.py`) — covers index/projection cache:
  `get_bead`, `put_bead`, `query_beads`, `get_associations`, etc. Two implementations:
  `JsonFileBackend` and `SqliteBackend`. Does NOT cover search or graph traversal.

- `VectorBackend` (in `retrieval/semantic_index.py`, selected by `CORE_MEMORY_VECTOR_BACKEND`) —
  covers semantic candidate search: FAISS (local) or pgvector. No relationship to
  `StorageBackend`; lives entirely inside the retrieval stack.

The retrieval pipeline (`retrieval/pipeline/canonical.py`) calls into `VectorBackend` for
semantic search and into Python graph traversal code (`graph/traversal.py`) for causal
chains. It cannot delegate either operation to a more capable backend (e.g., Neo4j) because
there is no way to express "this backend can also do traversal natively."

The architectural conclusion from external review: **Core Memory should define the bead
schema, causal edges, supersession logic, rolling window, and recall semantics. The
datastore should be pluggable with declared capability tiers.**

---

## Current state

| Component | Status |
|-----------|--------|
| `StorageBackend` Protocol | Done — covers persistence only |
| `JsonFileBackend` | Done |
| `SqliteBackend` | Done |
| `VectorBackend` (FAISS, pgvector) | Done — but separate silo |
| `BackendCapabilities` | Missing |
| `search_candidates` on `StorageBackend` | Missing |
| `traverse` on `StorageBackend` | Missing |
| `hydrate_turn_refs` on `StorageBackend` | Missing |
| Retrieval pipeline checks backend capabilities | Missing |

---

## Success criteria

1. `StorageBackend` (or a new `MemoryBackend` protocol) declares three new optional methods:
   `search_candidates`, `traverse`, `hydrate_turn_refs`.
2. `BackendCapabilities` dataclass exists and is returned by every backend via `.capabilities`.
3. `JsonFileBackend` and `SqliteBackend` declare all capability flags `False`; Python
   fallbacks fire exactly as they do today — no behavior change.
4. The retrieval pipeline checks `backend.capabilities.vector_search` before choosing
   between the backend method and the existing FAISS/pgvector path.
5. Existing `VectorBackend` continues to work as the fallback for backends with
   `vector_search=False`.
6. Full pytest suite passes with no behavior changes.

---

## Scope

**In:**
- Extend `StorageBackend` protocol with three new methods (with default `raise NotImplementedError`)
- Add `BackendCapabilities` dataclass to `persistence/backend.py`
- `.capabilities` property on `JsonFileBackend` and `SqliteBackend`
- Retrieval pipeline capability check (one conditional branch)
- Keeping `VectorBackend` as the fallback path (no removal)

**Out:**
- Neo4j backend implementation (that is Phase 7)
- pgvector integration changes
- Changes to recall semantics or scoring
- New capability tiers beyond the three listed

---

## Implementation

### New types in `persistence/backend.py`

```python
@dataclass
class BackendCapabilities:
    vector_search: bool = False
    graph_traversal: bool = False
    full_text_search: bool = False
    transcript_hydration: bool = False
```

### Extended protocol methods (add to `StorageBackend`)

```python
def capabilities(self) -> BackendCapabilities:
    """Declare which retrieval capabilities this backend can serve natively."""
    ...

def search_candidates(
    self,
    query_vec: list[float],
    filters: dict | None,
    limit: int,
) -> list[dict]:
    """Semantic candidate search. Only called if capabilities.vector_search is True."""
    raise NotImplementedError

def traverse(
    self,
    seed_ids: list[str],
    edge_types: list[str] | None,
    max_hops: int,
) -> list[dict]:
    """Graph traversal from seed beads. Only if capabilities.graph_traversal is True."""
    raise NotImplementedError

def hydrate_turn_refs(
    self,
    turn_refs: list[str],
) -> list[dict]:
    """Resolve turn IDs to full turn records. Only if capabilities.transcript_hydration."""
    raise NotImplementedError
```

### `JsonFileBackend` and `SqliteBackend`

Add `capabilities` property returning `BackendCapabilities()` (all False). Do not implement
the three new methods — let `NotImplementedError` propagate (unreachable since retrieval
pipeline checks capabilities first).

### Retrieval pipeline change (`retrieval/pipeline/canonical.py`)

At the semantic candidate search step:

```python
backend = store._backend  # already available
if backend.capabilities().vector_search:
    candidates = backend.search_candidates(query_vec, filters, limit)
else:
    candidates = _faiss_or_pgvector_search(query_vec, filters, limit)  # existing path
```

At the causal traversal step:

```python
if backend.capabilities().graph_traversal:
    chain = backend.traverse(seed_ids, edge_types, max_hops)
else:
    chain = _python_graph_traverse(seed_ids, edge_types, max_hops)  # existing path
```

This is the only change to the retrieval pipeline in this phase. The code path for all
existing backends is unchanged — they return `False` capabilities, so the else branches
always fire.

---

## Non-goals

- Do not remove or deprecate `VectorBackend`. It is the fallback path; backends with
  `vector_search=False` continue to use it.
- Do not change recall scoring, effort levels, or rolling window behavior.
- Do not introduce a `HybridBackend` wrapper in this phase. That is an optional future
  optimization.
