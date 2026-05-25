# PRD: Graph Backend Abstraction — Pluggable Causal Graph Providers

**Phase:** 7
**Status:** Not started
**Prerequisite:** Phase 6 complete (`BackendCapabilities` + extended `StorageBackend` protocol)
**Supersedes:** earlier scope of "Phase 7: Neo4j Query Backend" (now subsumed)

---

## Problem

Core Memory's causal traversal (`graph/traversal.py:causal_traverse_chains`) runs in Python
against the flat association list of the active projection cache. This is correct and
zero-dependency, but it does not scale, and it cannot leverage external graph engines —
even when one is connected. The Neo4j adapter (`integrations/neo4j/`) already mirrors
beads/associations one-way via `sync_to_neo4j`, but the recall pipeline never reads back
from Neo4j. The mirror is currently visualization-only.

Phase 6 added `BackendCapabilities.graph_traversal` and a `traverse()` stub on
`StorageBackend`, with `_caps.graph_traversal` branching in
`retrieval/pipeline/canonical.py`. The branch is unreachable today because no shipping
backend declares `graph_traversal=True`. Phase 7 wires the live branch and — critically —
does so through a **provider-pluggable abstraction**, not by hard-coding Neo4j.

The first-class targets are:

1. **Neo4j** — explicit, schema-controlled property graph. Cypher queries. We own the
   schema, the ingest mapping, and the query shape. Highest fidelity to the bead/causal
   ontology.
2. **Graphiti** (Zep's open-source temporal knowledge graph engine, built on Neo4j) — LLM-
   augmented temporal KG. We hand it episodes (turn/bead text) and it extracts its own
   entities + relationships. Lower-fidelity for causal beads but stronger for entity
   resolution and temporal reasoning.
3. **Zep Cloud** — managed Graphiti as a service. Same API shape as Graphiti, different
   transport (HTTPS + API key).

These providers have **different abstraction levels**. A single `Neo4jBackend` design
(seed_ids + edge_types) won't cleanly accommodate Graphiti's `add_episode` / `search`
contract. Phase 7 introduces a `GraphBackend` protocol distinct from `StorageBackend` —
one that admits both styles — and a provider registry that future backends (Memgraph,
Kuzu, JanusGraph, FalkorDB, custom internal services) can plug into without touching
core code.

---

## Architectural invariants

These are non-negotiable and must hold for every provider:

1. **Local storage remains source of truth.** Session JSONL files plus the local
   projection cache (`JsonFileBackend` or `SqliteBackend`) hold the canonical bead state.
   Graph backends are **mirrors and accelerators**, never primary stores. A
   `core-memory rebuild-projection` call must always work without the graph backend.
2. **No silent data loss.** If the graph backend rejects, fails, or lags, the write
   pipeline succeeds locally and the graph backend is marked degraded. The retrieval
   pipeline falls back to Python-side traversal automatically.
3. **`StorageBackend` and `GraphBackend` are separate abstractions.** Storage owns
   bead/association CRUD. Graph owns traversal + hybrid retrieval. They compose; neither
   subsumes the other.
4. **Capability negotiation is live, not just static.** A provider that is configured
   but unreachable reports `capabilities().graph_traversal=False` after the liveness
   probe. The pipeline never crashes on connection failure.
5. **Provider modules are optional installs.** `pip install core-memory` works with no
   graph backend. Providers are extras: `[neo4j]`, `[graphiti]`, `[zep]`.
6. **Layering law unchanged.** `persistence/graph/` lives under persistence. Retrieval
   and runtime can import it. Integrations cannot.

---

## What exists today

| Component                                          | Status      | Notes |
|----------------------------------------------------|-------------|-------|
| `BackendCapabilities` dataclass                    | Done (P6)   | `graph_traversal: bool` flag exists |
| `StorageBackend.traverse()` stub                   | Done (P6)   | Raises `NotImplementedError` |
| `_caps.graph_traversal` branch in canonical.py     | Done (P6)   | Unreachable — no provider sets it |
| `integrations/neo4j/client.py`                     | Done        | `Neo4jClient` — driver, upsert, prune |
| `integrations/neo4j/mapper.py`                     | Done        | `bead_to_node`, `association_to_edge` |
| `integrations/neo4j/sync.py`                       | Done        | One-way write path |
| `integrations/neo4j/config.py`                     | Done        | `Neo4jConfig`, env vars |
| **`persistence/graph/` package**                   | **Missing** | New — protocol + factory + providers |
| **`GraphBackend` protocol**                        | **Missing** | |
| **`NullGraphBackend`**                             | **Missing** | Default; declares zero capabilities |
| **`Neo4jGraphBackend`**                            | **Missing** | Read-side traverse via Cypher |
| **`GraphitiGraphBackend`**                         | **Missing** | Episode-based ingest + search |
| **`ZepGraphBackend`**                              | **Missing** | Thin wrapper over Zep Cloud API |
| **Provider registry + factory**                    | **Missing** | `create_graph_backend()` |
| **Retrieval-pipeline wiring**                      | **Missing** | Use `GraphBackend` not `StorageBackend.traverse` |
| **Write-side hook (`on_bead_written`)**            | **Missing** | Replaces direct `sync_to_neo4j` calls |
| **Bulk sync CLI (`core-memory graph-sync`)**       | **Missing** | One-shot backfill of existing index |

---

## Success criteria

When Phase 7 is complete:

1. `from core_memory.persistence.graph import create_graph_backend, GraphBackend` works.
2. `CORE_MEMORY_GRAPH_BACKEND=none` (default) returns `NullGraphBackend` —
   `capabilities()` all-False. Existing behavior unchanged. No new dependencies required.
3. `CORE_MEMORY_GRAPH_BACKEND=neo4j` returns `Neo4jGraphBackend`. With a live Neo4j
   instance synced, `traverse(seed_ids, edge_types, max_hops)` executes a Cypher
   variable-length path query and returns chains in the same shape as
   `causal_traverse_chains`.
4. `CORE_MEMORY_GRAPH_BACKEND=graphiti` returns `GraphitiGraphBackend`. With a live
   Graphiti instance, `search_candidates(query_text=..., ...)` returns candidate beads
   ranked by Graphiti's temporal KG retrieval.
5. `CORE_MEMORY_GRAPH_BACKEND=zep` returns `ZepGraphBackend` (managed-service variant).
6. The retrieval pipeline (`canonical.py:trace_request` and `search_request`) consults
   `GraphBackend.capabilities()` before deciding whether to use the provider or fall back
   to the Python-side implementation. The fallback is always available.
7. When a configured provider is unreachable (Neo4j down, API timeout), `capabilities()`
   returns False for the affected operations and the pipeline degrades silently. A
   single `provider_unhealthy` warning surfaces in the recall result.
8. `process_turn_finalized` calls `graph_backend.on_bead_written(bead)` /
   `on_association_written(assoc)` after the local write succeeds. Provider failures here
   are logged and surfaced as warnings but never block the local write.
9. `core-memory graph-sync --provider=neo4j --root=.` performs a one-shot backfill from
   the local projection into the graph backend. Idempotent. Reports counts.
10. Tests pass for every provider with both a fake/mock backend and (gated) a real backend
    in CI. Provider-specific tests are marked `@pytest.mark.neo4j`, `@pytest.mark.graphiti`,
    `@pytest.mark.zep` and skipped without the corresponding env vars / Docker service.

---

## The `GraphBackend` protocol

```python
# core_memory/persistence/graph/protocol.py
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable

from core_memory.persistence.backend import BackendCapabilities


@runtime_checkable
class GraphBackend(Protocol):
    """Pluggable graph provider for traversal + hybrid retrieval.

    Distinct from StorageBackend. Storage owns bead/association CRUD against the
    source-of-truth projection. Graph backends are query-side accelerators and
    (optionally) write-side mirrors. They never replace local storage.
    """

    name: str  # "null" | "neo4j" | "graphiti" | "zep" | <custom>

    def capabilities(self) -> BackendCapabilities:
        """Live capability declaration. Must reflect health probe result.

        A configured-but-unreachable provider returns all-False capabilities
        until reachability is restored.
        """
        ...

    def health(self) -> dict[str, Any]:
        """Liveness probe. Returns {"ok": bool, "latency_ms": int, "warnings": [...]}.

        Called on a TTL (default 60s) by the capabilities() implementation.
        """
        ...

    # ---- Read-side ------------------------------------------------------

    def traverse(
        self,
        seed_ids: list[str],
        edge_types: list[str] | None,
        max_hops: int,
        max_chains: int = 5,
    ) -> dict[str, Any]:
        """Walk the causal graph from seed beads.

        Result shape mirrors graph.traversal.causal_traverse_chains:
        {"ok": bool, "chains": [{"nodes": [...], "edges": [...]}], "warnings": [...]}

        Providers that cannot serve traversal (capability=False) raise NotImplementedError.
        """
        raise NotImplementedError

    def search_candidates(
        self,
        *,
        query_text: str,
        query_vec: list[float] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 24,
    ) -> dict[str, Any]:
        """Hybrid retrieval — text/vector + graph proximity.

        Used by providers that combine semantic + graph signals (Graphiti, Zep).
        Pure-Neo4j returns NotImplementedError; semantic search stays on FAISS/pgvector.

        Result shape:
        {"ok": bool, "results": [{"bead_id": str, "score": float, ...}], "warnings": [...]}
        """
        raise NotImplementedError

    def hydrate_turn_refs(self, turn_refs: list[str]) -> list[dict[str, Any]]:
        """Resolve turn IDs to full turn records via the graph backend.

        Optional. Most providers leave this on the storage backend.
        """
        raise NotImplementedError

    # ---- Write-side -----------------------------------------------------

    def on_bead_written(self, bead: dict[str, Any]) -> dict[str, Any]:
        """Notify provider of a new/updated bead. Idempotent.

        Returns {"ok": bool, "warnings": [...]}. Failures are logged but do not
        block the local write. Called from process_turn_finalized after local
        storage write succeeds.
        """
        return {"ok": True, "warnings": []}

    def on_association_written(self, assoc: dict[str, Any]) -> dict[str, Any]:
        """Notify provider of a new association. Idempotent."""
        return {"ok": True, "warnings": []}

    def on_bead_retracted(self, bead_id: str) -> dict[str, Any]:
        """Notify provider of a retraction / status change."""
        return {"ok": True, "warnings": []}

    # ---- Bulk -----------------------------------------------------------

    def sync_from_storage(
        self,
        storage: "StorageBackend",  # forward ref to persistence.backend
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """One-shot backfill: read full index from storage, push to provider.

        Idempotent. Returns {"ok": bool, "beads_synced": int, "assocs_synced": int}.
        Called by `core-memory graph-sync` and on first provider activation.
        """
        ...

    def close(self) -> None:
        """Release driver / HTTP client resources."""
        ...
```

### Why a new protocol instead of extending `StorageBackend`

- **Different lifecycle.** Storage is required; graph is optional. Composing them in one
  Protocol forces every storage backend to think about graph concerns, and vice versa.
- **Different write semantics.** Storage writes are synchronous and atomic. Graph writes
  are best-effort, fire-and-forget at the boundary, and provider-specific (Neo4j MERGE
  vs. Graphiti `add_episode`).
- **Different failure model.** Storage failure = data loss. Graph failure = degraded
  retrieval only.
- **Phase 6 already exposes `BackendCapabilities` as a shared vocabulary.** Both
  protocols can return it without coupling.

`StorageBackend.traverse()` (added in Phase 6) remains for future backends that genuinely
unify storage + graph (e.g., a hypothetical embedded graph DB). Phase 7 does **not**
populate it for json/sqlite; those continue to return False.

---

## Provider designs

### NullGraphBackend (default)

```python
# core_memory/persistence/graph/null.py
class NullGraphBackend:
    name = "null"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()

    def health(self) -> dict:
        return {"ok": True, "latency_ms": 0, "warnings": []}

    # All read-side methods raise NotImplementedError.
    # All write-side methods return {"ok": True, "warnings": []} (no-op).
    def on_bead_written(self, bead): return {"ok": True, "warnings": []}
    def on_association_written(self, assoc): return {"ok": True, "warnings": []}
    def on_bead_retracted(self, bead_id): return {"ok": True, "warnings": []}

    def sync_from_storage(self, storage, *, dry_run=False):
        return {"ok": True, "beads_synced": 0, "assocs_synced": 0, "warnings": ["null_backend"]}

    def close(self): pass
```

This is the default. No env var → null provider → zero behavior change. The retrieval
pipeline already handles `capabilities().graph_traversal=False` by falling back.

### Neo4jGraphBackend

```python
# core_memory/persistence/graph/neo4j_backend.py
class Neo4jGraphBackend:
    name = "neo4j"

    def __init__(self, config: Neo4jConfig):
        from core_memory.integrations.neo4j.client import Neo4jClient
        self._client = Neo4jClient(config)
        self._healthy = None  # set by health()
        self._health_checked_at = 0.0
        self._health_ttl_s = 60.0

    def capabilities(self) -> BackendCapabilities:
        if self._needs_recheck():
            self._healthy = bool(self.health().get("ok"))
        if not self._healthy:
            return BackendCapabilities()
        return BackendCapabilities(graph_traversal=True)

    def health(self) -> dict:
        # `RETURN 1` with 100ms timeout. Update self._healthy.
        ...

    def traverse(self, seed_ids, edge_types, max_hops, max_chains=5):
        # Single Cypher query, params: seed_ids, edge_types, max_hops, max_chains
        # MATCH path = (start:Bead)-[r*1..$max_hops]->(n:Bead)
        # WHERE start.id IN $seed_ids
        #   AND ($edge_types IS NULL OR ALL(rel IN relationships(path) WHERE type(rel) IN $edge_types))
        # RETURN path
        # LIMIT $max_chains
        #
        # Map results to {"nodes":[bead_dicts], "edges":[{"rel": type, "src": id, "tgt": id}]}
        ...

    def search_candidates(self, **kw):
        raise NotImplementedError  # Phase 7 scope: traversal only

    def on_bead_written(self, bead):
        # MERGE (b:Bead {id: $id}) SET b += $props
        # Reuses mapper.bead_to_node, with idempotent MERGE semantics.
        ...

    def on_association_written(self, assoc):
        # MATCH (s:Bead {id: $src}), (t:Bead {id: $tgt})
        # MERGE (s)-[r:`<rel_type>`]->(t) SET r += $props
        ...

    def sync_from_storage(self, storage, *, dry_run=False):
        # Iterate storage.query_beads({}), call on_bead_written for each.
        # Then storage.get_associations(), call on_association_written for each.
        # Track counts. Return summary.
        ...

    def close(self):
        self._client.close()
```

Key Cypher query for traversal:

```cypher
MATCH path = (start:Bead)-[r*1..$max_hops]->(n:Bead)
WHERE start.id IN $seed_ids
  AND ($edge_types IS NULL
       OR ALL(rel IN relationships(path) WHERE type(rel) IN $edge_types))
WITH path, n,
     [rel IN relationships(path) | type(rel)] AS edge_path,
     length(path) AS depth
ORDER BY depth ASC
LIMIT $max_chains
RETURN
  [node IN nodes(path) | node {.id, .type, .title, .summary, .created_at}] AS nodes,
  [rel IN relationships(path)
     | {rel: type(rel), src: startNode(rel).id, tgt: endNode(rel).id}] AS edges
```

Mapping must match `causal_traverse_chains` output exactly so downstream consumers
(`canonical.py:chains`) are oblivious to the provider.

### GraphitiGraphBackend

```python
# core_memory/persistence/graph/graphiti_backend.py
class GraphitiGraphBackend:
    name = "graphiti"

    def __init__(self, config: GraphitiConfig):
        # Lazy import of graphiti_core (optional dep).
        from graphiti_core import Graphiti  # type: ignore
        self._client = Graphiti(
            neo4j_uri=config.neo4j_uri,
            neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password,
            llm_client=config.build_llm_client(),
        )
        self._healthy = None

    def capabilities(self) -> BackendCapabilities:
        if not self._is_healthy():
            return BackendCapabilities()
        # Graphiti gives us both graph traversal (via Neo4j underneath)
        # AND semantic+temporal search.
        return BackendCapabilities(
            graph_traversal=True,
            vector_search=True,
            full_text_search=True,
        )

    def search_candidates(self, *, query_text, query_vec=None, filters=None, limit=24):
        # results = await self._client.search(query=query_text, num_results=limit)
        # Map Graphiti EdgeSearchResult / NodeSearchResult to our bead-shape dicts.
        # Bead ID resolution: episodes are stored with name=bead_id.
        ...

    def traverse(self, seed_ids, edge_types, max_hops, max_chains=5):
        # Graphiti exposes get_nodes_by_query / get_episodes — limited compared to Cypher.
        # For Phase 7 we run a constrained Cypher query through Graphiti's driver,
        # using our own :Bead label namespace (we keep Graphiti's auto-extracted
        # graph in its own namespace, and our beads in ours).
        ...

    def on_bead_written(self, bead):
        # Two-mode write:
        # Mode A (default): bead → episode via add_episode(...).
        #   Graphiti runs its LLM extractor and produces a temporal KG of
        #   entities/relationships from the bead text.
        # Mode B (raw): also MERGE the bead as a :Bead node (shared with Neo4j path)
        #   so traverse() works against our explicit schema.
        # Phase 7 implements Mode B; Mode A is opt-in via config flag.
        ...

    def sync_from_storage(self, storage, *, dry_run=False):
        # Iterate beads → add_episode (Mode A) and/or MERGE :Bead (Mode B).
        # Iterate associations → MERGE typed edges (Mode B only).
        ...
```

**Trade-offs to surface in the doc.** Graphiti is opinionated. Its strength is auto-
extraction of entities/relationships from natural language episodes. Its weakness for
our case is that we already have explicit causal typing — we don't want Graphiti to
re-extract and risk conflicting with our schema. Hence the dual-mode write:

- **Mode A (LLM extraction):** lean into Graphiti's strengths. Use it for entity
  resolution and temporal queries. Our beads remain the source of truth in local
  storage; Graphiti is a side index.
- **Mode B (explicit schema):** treat Graphiti's Neo4j as a regular Neo4j and ignore
  the LLM extractor. Phase 7's `traverse()` uses Mode B.

Both modes coexist on the same Graphiti instance (different node labels). The PRD must
make this explicit so a user choosing Graphiti understands the cost (LLM calls on every
write in Mode A) and the value (entity-level semantic retrieval).

### ZepGraphBackend

Same surface as `GraphitiGraphBackend`, but over Zep's HTTPS API:

```python
# core_memory/persistence/graph/zep_backend.py
class ZepGraphBackend:
    name = "zep"

    def __init__(self, config: ZepConfig):
        from zep_cloud.client import AsyncZep  # type: ignore
        self._client = AsyncZep(api_key=config.api_key, base_url=config.base_url)
        self._session_id = config.session_id  # core-memory's notion of a "user" / "agent"

    def on_bead_written(self, bead):
        # await self._client.memory.add(session_id=self._session_id, messages=[...])
        # We map a bead to a Zep "message" with role="system" and content=bead.summary.
        ...

    def search_candidates(self, *, query_text, **kw):
        # await self._client.memory.search_sessions(text=query_text, ...)
        ...

    # traverse() returns NotImplementedError in Phase 7; Zep does not expose Cypher.
    # Capabilities for Phase 7: vector_search=True, graph_traversal=False.
```

Zep is the lightest provider — no infrastructure to run, fewest knobs, no Mode A/B
choice. It is intentionally scoped to `search_candidates` only. Causal traversal
remains on the Python fallback when Zep is selected.

---

## Provider registry and factory

```python
# core_memory/persistence/graph/factory.py
import os
from typing import Callable

from .protocol import GraphBackend
from .null import NullGraphBackend

_PROVIDERS: dict[str, Callable[[], GraphBackend]] = {
    "none": NullGraphBackend,
    "null": NullGraphBackend,
}


def register_graph_backend(name: str, factory: Callable[[], GraphBackend]) -> None:
    """Plugin hook for custom providers. Idempotent re-registration is allowed."""
    _PROVIDERS[name.strip().lower()] = factory


def create_graph_backend() -> GraphBackend:
    name = (os.environ.get("CORE_MEMORY_GRAPH_BACKEND") or "none").strip().lower()
    factory = _PROVIDERS.get(name)
    if factory is None:
        # Unknown provider: log warning, return null. Never raise at module import.
        return NullGraphBackend()
    try:
        return factory()
    except Exception:
        # Provider construction failed (missing dep, bad config). Degrade.
        return NullGraphBackend()


# First-party providers register themselves at import time, but the import is lazy.
def _register_first_party() -> None:
    try:
        from .neo4j_backend import Neo4jGraphBackend, neo4j_factory
        register_graph_backend("neo4j", neo4j_factory)
    except ImportError:
        pass
    try:
        from .graphiti_backend import GraphitiGraphBackend, graphiti_factory
        register_graph_backend("graphiti", graphiti_factory)
    except ImportError:
        pass
    try:
        from .zep_backend import ZepGraphBackend, zep_factory
        register_graph_backend("zep", zep_factory)
    except ImportError:
        pass


_register_first_party()
```

Each provider module is gated behind its optional dependency. Missing `neo4j` package?
The `from .neo4j_backend import ...` line raises `ImportError`, the registration is
skipped, and `CORE_MEMORY_GRAPH_BACKEND=neo4j` falls through to `NullGraphBackend`. This
is the same pattern as the rest of the codebase (Anthropic / OpenAI / FAISS imports).

---

## Retrieval pipeline wiring

In `retrieval/pipeline/canonical.py`, replace the Phase 6 placeholder branch:

```python
# Phase 6 (current):
_caps = get_backend_capabilities(rp / ".beads")
...
if _caps.graph_traversal:
    trav = {"ok": True, "chains": []}  # backend.traverse not yet wired
else:
    trav = causal_traverse(Path(root), anchor_ids=a_ids, max_depth=3, max_chains=5) ...
```

```python
# Phase 7:
from core_memory.persistence.graph import create_graph_backend
graph_backend = create_graph_backend()  # process-scope cached; see below
caps = graph_backend.capabilities()
...
if caps.graph_traversal and a_ids:
    trav = graph_backend.traverse(
        seed_ids=a_ids,
        edge_types=None,
        max_hops=3,
        max_chains=5,
    )
    if not trav.get("ok"):
        trav = causal_traverse(Path(root), anchor_ids=a_ids, max_depth=3, max_chains=5)
else:
    trav = causal_traverse(Path(root), anchor_ids=a_ids, max_depth=3, max_chains=5) if a_ids \
           else {"ok": True, "chains": []}
```

`graph_backend` is process-scope, cached via `functools.lru_cache()` keyed on env-var
snapshot. Re-reading the env var on every request would defeat caching; we accept a
process restart as the way to change providers.

The same shape applies to `search_request` for `search_candidates`:

```python
if caps.full_text_search or caps.vector_search:
    sem = graph_backend.search_candidates(query_text=expanded_query, query_vec=None, limit=sem_k)
    if not sem.get("ok"):
        sem = semantic_lookup(rp, expanded_query, k=sem_k, mode=_canonical_semantic_mode())
else:
    sem = semantic_lookup(...)  # unchanged Phase 6 path
```

---

## Write-side hook

In `runtime/process_turn_finalized` (and equivalent for associations), after the local
write succeeds, fire the graph hook:

```python
# core_memory/runtime/write_path.py (or wherever the canonical write lives)
def _emit_to_graph_backend(bead: dict) -> list[str]:
    """Best-effort mirror to graph backend. Never raises."""
    try:
        from core_memory.persistence.graph import create_graph_backend
        gb = create_graph_backend()
        result = gb.on_bead_written(bead)
        if not result.get("ok"):
            return [f"graph_backend_warning:{gb.name}"]
        return []
    except Exception as exc:
        return [f"graph_backend_error:{type(exc).__name__}"]
```

This is the **single canonical write hook** for the graph layer. The existing
`integrations/neo4j/sync.py:sync_to_neo4j` is deprecated as an in-pipeline call site —
it becomes the implementation detail of `Neo4jGraphBackend.on_bead_written`. The
deprecation is silent at first (Phase 7), with a deprecation warning added in Phase 8
once all call sites have moved.

Warnings from `_emit_to_graph_backend` attach to the recall result's `warnings` list
under a `graph_backend.<provider>.<error>` namespace so the test harness can observe
degradation without crashing.

---

## Bulk sync CLI

```
$ core-memory graph-sync --provider=neo4j --root=. [--dry-run]
```

```python
# core_memory/cli_handlers_graph_sync.py
def handle_graph_sync(args) -> int:
    storage = create_backend(Path(args.root) / ".beads")
    gb = create_graph_backend()
    if gb.name in ("null", "none"):
        print(f"No graph backend configured. Set CORE_MEMORY_GRAPH_BACKEND.")
        return 2
    result = gb.sync_from_storage(storage, dry_run=bool(args.dry_run))
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1
```

Run on first provider activation (the user enables Neo4j on an existing repo) and any
time the local projection diverges from the graph backend (after a `rebuild-projection`).

---

## Configuration surface

```
# Provider selection (default: none → NullGraphBackend, zero deps)
CORE_MEMORY_GRAPH_BACKEND = none | neo4j | graphiti | zep | <custom>

# Neo4j (also reused by Graphiti when graphiti backend selected)
CORE_MEMORY_NEO4J_URI       = bolt://localhost:7687
CORE_MEMORY_NEO4J_USER      = neo4j
CORE_MEMORY_NEO4J_PASSWORD  = ...
CORE_MEMORY_NEO4J_TLS       = 0|1
CORE_MEMORY_NEO4J_TIMEOUT_MS = 5000

# Graphiti-specific
CORE_MEMORY_GRAPHITI_MODE   = explicit | extracted | both    # default: explicit
CORE_MEMORY_GRAPHITI_LLM_PROVIDER = anthropic | openai       # for entity extraction
CORE_MEMORY_GRAPHITI_LLM_MODEL    = claude-haiku-4-5-20251001

# Zep Cloud
CORE_MEMORY_ZEP_API_KEY     = ...
CORE_MEMORY_ZEP_BASE_URL    = https://api.getzep.com
CORE_MEMORY_ZEP_SESSION_ID  = core-memory-default   # the "user" identifier

# Health probe cadence (shared)
CORE_MEMORY_GRAPH_HEALTH_TTL_S = 60
```

`Neo4jConfig`, `GraphitiConfig`, `ZepConfig` dataclasses live next to their respective
provider modules. Each provides a `from_env()` classmethod for the factory.

---

## Test strategy

### Unit tests (no external services)

- `tests/test_graph_backend_protocol.py` — `NullGraphBackend` returns all-False caps,
  no-op write hooks, `sync_from_storage` returns ok with zero counts.
- `tests/test_graph_backend_factory.py` — `create_graph_backend()` with each env-var
  value, missing-dep behavior (force `ImportError`), unknown-provider fallback to null.
- `tests/test_graph_backend_capabilities.py` — degraded health → all-False caps;
  health-recovery → caps restored after TTL expiry.

### Provider tests with fakes

- `tests/test_neo4j_graph_backend_fake.py` — `Neo4jClient` mocked to return fixture
  rows; verify Cypher params, result mapping, traversal shape, on_bead_written MERGE
  semantics.
- `tests/test_graphiti_graph_backend_fake.py` — `graphiti_core.Graphiti` mocked;
  verify episode payloads (Mode A) and explicit-schema writes (Mode B).
- `tests/test_zep_graph_backend_fake.py` — `AsyncZep` mocked; verify message
  payloads and search call shape.

### Provider tests with live services (gated)

- `tests/test_neo4j_graph_backend_live.py` — `@pytest.mark.neo4j`, requires
  `CORE_MEMORY_NEO4J_URI` env. Docker Compose file at `tests/docker-compose.neo4j.yml`.
  Covers: bulk sync, 1-hop, 3-hop, edge-type filter, empty graph, disconnected seed,
  reachability outage mid-test.
- `tests/test_graphiti_graph_backend_live.py` — `@pytest.mark.graphiti`, requires
  `CORE_MEMORY_NEO4J_URI` + `CORE_MEMORY_GRAPHITI_LLM_API_KEY`. Smoke test for episode
  ingest + retrieval.
- `tests/test_zep_graph_backend_live.py` — `@pytest.mark.zep`, requires
  `CORE_MEMORY_ZEP_API_KEY`. Hits Zep Cloud sandbox. Cleaned up via session deletion.

### Pipeline integration tests

- `tests/test_retrieval_uses_graph_backend.py` — patches `create_graph_backend` to
  return a fake `Neo4jGraphBackend`, runs `trace_request`, asserts `traverse()` was
  called instead of `causal_traverse`, asserts result shape unchanged for callers.
- `tests/test_retrieval_falls_back_on_graph_failure.py` — fake backend returns
  `{"ok": False}` from `traverse`; pipeline falls back to Python and surfaces the
  expected warning.

### CI matrix

| Job                | Providers tested | Marker                | Required env                  |
|--------------------|------------------|----------------------|--------------------------------|
| default            | null             | (unmarked)            | none                           |
| neo4j-live         | neo4j            | `@pytest.mark.neo4j`  | Docker Compose Neo4j 5         |
| graphiti-live      | graphiti         | `@pytest.mark.graphiti` | Neo4j + LLM API key         |
| zep-live           | zep              | `@pytest.mark.zep`    | `CORE_MEMORY_ZEP_API_KEY`      |

The default CI job blocks merges and runs everywhere. The live jobs run on push to
main and on PRs that touch `persistence/graph/` or the corresponding integration
modules.

---

## Implementation phasing

| Sub-phase | Deliverable                                                       | Gate            |
|-----------|--------------------------------------------------------------------|-----------------|
| **7a**    | `persistence/graph/` package skeleton: protocol + `NullGraphBackend` + factory + retrieval pipeline wiring. Behavior unchanged (null returns all-False). | Phase 6 done |
| **7b**    | `Neo4jGraphBackend.traverse()` + `health()` + `capabilities()`. Read-side only. Reuses existing `Neo4jClient`. Live tests gated. | 7a |
| **7c**    | Write-side hook (`on_bead_written`, `on_association_written`) on `Neo4jGraphBackend`. Migrates `sync_to_neo4j` callers to the hook. | 7b |
| **7d**    | `core-memory graph-sync` CLI. Bulk backfill. Idempotent. | 7c |
| **7e**    | `GraphitiGraphBackend` (Mode B first — explicit schema). | 7c |
| **7f**    | `GraphitiGraphBackend` Mode A — episode-based ingest with LLM extraction. Behind `CORE_MEMORY_GRAPHITI_MODE=extracted`. | 7e |
| **7g**    | `ZepGraphBackend` — managed-service variant. `search_candidates` only; traversal NotImplemented. | 7c |
| **7h**    | Document the plugin API for third-party providers (`register_graph_backend`). | 7g |

Each sub-phase is independently shippable. 7a alone makes the existing Python fallback
the explicit default with no behavior change. 7b makes Neo4j production-useful for
traversal. 7c and 7d close the write loop. 7e–7g add the alternative providers. 7h is
documentation only.

---

## Backwards compatibility and migration

- **No env var set → no change.** `NullGraphBackend` is the default. Existing users
  see identical behavior. No new dependencies are required at install time.
- **Existing Neo4j users.** Today they call `sync_to_neo4j` from their own code or via
  a periodic job. Phase 7c moves this to a write-side hook, but the standalone
  `sync_to_neo4j` function is retained as a thin wrapper around
  `Neo4jGraphBackend.sync_from_storage` to avoid breaking external scripts. A
  deprecation warning fires after Phase 8.
- **`StorageBackend.traverse()` stub.** Remains for future "unified storage + graph"
  backends. Today and through Phase 7, json/sqlite continue to return
  `BackendCapabilities()` from their `capabilities()` methods and
  `NotImplementedError` from `traverse()`. The retrieval pipeline consults
  `GraphBackend.capabilities()`, not `StorageBackend.capabilities().graph_traversal`,
  for the routing decision.

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| **Graphiti LLM-extraction cost.** Every bead write triggers LLM calls in Mode A. | Mode A is opt-in. Default is Mode B (explicit schema, no LLM). Document cost up front. |
| **Schema drift between providers.** Neo4j's `:Bead` label and Graphiti's auto-extracted nodes coexist; queries might cross-pollinate. | Namespace all our writes under a fixed label set (`:Bead`, `:Turn`, `:Association`). Document that Graphiti's auto-extracted graph is a side index. |
| **Traversal result shape mismatch.** Provider returns chains with subtly different fields than Python `causal_traverse_chains`. | Define `GraphTraversalResult` TypedDict in `persistence/graph/types.py`. Every provider runs `_normalize_traversal_result` before returning. Contract-tested. |
| **Live tests flaky in CI.** Docker Compose Neo4j slow to boot. | Live tests are gated and run async to the default suite. Default CI stays green on null backend only. |
| **Provider connection holds linger.** Tests forget `close()`, leaving sockets. | Factory returns a singleton; harness teardown calls `close()` in a `conftest` fixture. |
| **Zep session model doesn't fit core-memory's session_id.** | Map `CORE_MEMORY_ZEP_SESSION_ID` env to Zep "session", treat core-memory session_id as a metadata field on each message. Document the mismatch. |

---

## Out of scope (deferred to later phases)

- **Memgraph, Kuzu, FalkorDB, JanusGraph, AWS Neptune adapters.** The plugin API in
  7h is enough; third parties can ship these out-of-tree.
- **Hybrid retrieval reranking that combines graph signal with FAISS scores.** Already
  partly handled by `run_hybrid_rerank_seeds`; deeper integration with provider-side
  graph scores is Phase 11+.
- **Multi-provider routing** (e.g., Neo4j for traversal + Zep for entity search in
  parallel). Phase 7 is single-provider per process. Multi-provider composition is a
  future phase.
- **Graph-side claim resolution.** Claims are a domain concept; pushing claim
  resolution into Cypher / Graphiti is a separate research arc, not Phase 7.

---

## Open questions

1. **Should `GraphBackend.on_bead_written` be async?** Most providers' write APIs are
   async. Phase 7 keeps it sync for simplicity (drivers expose sync wrappers); revisit
   if write latency becomes a problem.
2. **How does the rolling-window compaction interact with graph backends?** When a
   bead is archived or compressed, should the graph backend retain it? Default: yes
   (the graph is an append-only audit log of writes); revisit if storage costs spike.
3. **Provider observability.** Do we add a `GraphBackend.metrics()` method for
   per-provider operation counts / latency histograms? Phase 7 punts; structured logs
   are sufficient initially.
