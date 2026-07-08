# PRD: Graph Backend Abstraction — Pluggable Causal Graph Providers

**Phase:** 7
**Status:** Complete through 7i; live provider tests remain env-gated
**Prerequisite:** Phase 6 complete (`BackendCapabilities` + extended `StorageBackend` protocol)
**Supersedes:** earlier scope of "Phase 7: Neo4j Query Backend" (now subsumed)

---

## Implementation status

This PRD started as the design target for Phase 7. The current tree has shipped
the provider abstraction and first-party providers:

- `core_memory.persistence.graph` now exposes `GraphBackend`,
  `NullGraphBackend`, `create_graph_backend`, and `register_graph_backend`.
- Kuzu, Neo4j, Graphiti, and Zep-backed Graphiti providers are implemented.
- `core-memory graph backend-sync` is the supported bulk sync command.
- Obsidian shipped as a `BeadSyncTarget` mirror, not as a `GraphBackend`,
  because it has no reliable programmatic traversal API.
- Provider plugin documentation lives in `docs/graph_backend_plugin.md`.

Live provider checks remain environment-gated; the always-on suite uses mocked,
fake, and local embedded coverage.

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
2. **Graphiti / Zep** — Graphiti is an open-source LLM-augmented temporal knowledge graph
   engine built on Neo4j. Zep is the paid managed hosting of Graphiti — same library, same
   API, different transport (self-hosted bolt vs. Zep Cloud HTTPS + API key). One
   `GraphitiGraphBackend` class with a `deployment` config field handles both.
3. **Obsidian** — local markdown vault with wikilink-based graph. Write-side: beads become
   `.md` files with YAML frontmatter and `[[wikilink]]` associations. Read-side: optional
   Local REST API plugin for full-text search. Primary value is human-browsable
   visualization and AI-assisted note synthesis, not programmatic traversal.

These providers have **different abstraction levels**. A single `Neo4jBackend` design
(seed_ids + edge_types) won't cleanly accommodate Graphiti's `add_episode` / `search`
contract, and neither fits Obsidian's file-per-bead model. Phase 7 introduces a
`GraphBackend` protocol distinct from `StorageBackend` — one that admits all three styles —
and a provider registry that future backends (Memgraph, Kuzu, JanusGraph, FalkorDB,
custom internal services) can plug into without touching core code.

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

## Implementation inventory

| Component                                          | Status      | Notes |
|----------------------------------------------------|-------------|-------|
| `BackendCapabilities` dataclass                    | Done (P6)   | `graph_traversal: bool` flag exists |
| `StorageBackend.traverse()` stub                   | Done (P6)   | Raises `NotImplementedError` |
| `_caps.graph_traversal` branch in canonical.py     | Done        | Uses graph backend capabilities when a provider is active |
| `integrations/neo4j/client.py`                     | Done        | `Neo4jClient` — driver, upsert, prune |
| `integrations/neo4j/mapper.py`                     | Done        | `bead_to_node`, `association_to_edge` |
| `integrations/neo4j/sync.py`                       | Done        | One-way write path |
| `integrations/neo4j/config.py`                     | Done        | `Neo4jConfig`, env vars |
| **`persistence/graph/` package**                   | **Done** | Protocol, factory, and providers |
| **`GraphBackend` protocol**                        | **Done** | `core_memory.persistence.graph.protocol.GraphBackend` |
| **`NullGraphBackend`**                             | **Done** | Explicit/fallback no-op provider |
| **`KuzuGraphBackend`**                             | **Done** | Embedded graph backend |
| **`Neo4jGraphBackend`**                            | **Done** | Read/write path with mocked and live-gated tests |
| **`GraphitiGraphBackend`**                         | **Done** | Self-hosted + Zep-hosted mode |
| **`ObsidianSyncTarget`**                           | **Done** | Markdown vault write mirror via `BeadSyncTarget`; not a graph traversal provider |
| **Provider registry + factory**                    | **Done** | `create_graph_backend()` and `register_graph_backend()` |
| **Retrieval-pipeline wiring**                      | **Done** | Uses `GraphBackend` capabilities before fallback |
| **Write-side hook (`on_bead_written`)**            | **Done** | Runtime post-write boundary calls graph/sync targets after durable write |
| **Bulk sync CLI (`core-memory graph backend-sync`)** | **Done** | One-shot backfill of existing index |

---

## Success criteria

Current Phase 7 completion criteria:

1. `from core_memory.persistence.graph import create_graph_backend, GraphBackend` works.
2. Unset `CORE_MEMORY_GRAPH_BACKEND` defaults to embedded `KuzuGraphBackend`.
   `CORE_MEMORY_GRAPH_BACKEND=none` returns `NullGraphBackend` with
   `capabilities()` all-False.
3. `CORE_MEMORY_GRAPH_BACKEND=neo4j` returns `Neo4jGraphBackend`. With a live Neo4j
   instance synced, `traverse(seed_ids, edge_types, max_hops)` executes a Cypher
   variable-length path query and returns chains in the same shape as
   `causal_traverse_chains`.
4. `CORE_MEMORY_GRAPH_BACKEND=graphiti` returns `GraphitiGraphBackend` in self-hosted mode
   (requires Neo4j + `graphiti-core`). `search_candidates(query_text=..., ...)` returns
   candidate beads ranked by Graphiti's temporal KG retrieval.
   `CORE_MEMORY_GRAPHITI_DEPLOYMENT=hosted` (or `CORE_MEMORY_GRAPH_BACKEND=zep` as alias)
   switches the same class to Zep Cloud transport (HTTPS + API key, no local Neo4j needed).
5. Obsidian integration is provided through `ObsidianSyncTarget` and the
   `BeadSyncTarget` protocol. With a configured vault path,
   `on_bead_written` writes a `.md` file with YAML frontmatter and wikilinks.
   Obsidian is not advertised as a graph traversal backend.
6. The retrieval pipeline (`canonical.py:trace_request` and `search_request`) consults
   `GraphBackend.capabilities()` before deciding whether to use the provider or fall back
   to the Python-side implementation. The fallback is always available.
7. When a configured provider is unreachable (Neo4j down, API timeout), `capabilities()`
   returns False for the affected operations and the pipeline degrades silently. A
   single `provider_unhealthy` warning surfaces in the recall result.
8. `process_turn_finalized` calls `graph_backend.on_bead_written(bead)` /
   `on_association_written(assoc)` after the local write succeeds. Provider failures here
   are logged and surfaced as warnings but never block the local write.
9. `core-memory graph backend-sync --root=.` performs a one-shot backfill from
   the local projection into the configured graph backend. Idempotent. Reports counts.
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
        Called by `core-memory graph backend-sync` and on first provider activation.
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

### NullGraphBackend (explicit fallback)

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

### GraphitiGraphBackend (self-hosted and Zep-hosted)

Graphiti and Zep are the same codebase. Graphiti is the open-source library
(`graphiti-core` on PyPI). Zep is the managed hosting of that same library — same API
shape, same episode model, different transport. One class handles both deployments via a
`deployment` field.

```python
# core_memory/persistence/graph/graphiti_backend.py
from enum import Enum

class GraphitiDeployment(str, Enum):
    SELF_HOSTED = "self_hosted"   # local graphiti-core + your own Neo4j
    HOSTED = "hosted"             # Zep Cloud (HTTPS + API key, no local Neo4j)

class GraphitiGraphBackend:
    name = "graphiti"

    def __init__(self, config: GraphitiConfig):
        self._config = config
        self._healthy = None
        if config.deployment == GraphitiDeployment.HOSTED:
            # Zep Cloud path — uses zep-python SDK
            from zep_cloud.client import AsyncZep  # type: ignore
            self._client = AsyncZep(api_key=config.api_key, base_url=config.base_url)
        else:
            # Self-hosted path — uses graphiti-core directly
            from graphiti_core import Graphiti  # type: ignore
            self._client = Graphiti(
                neo4j_uri=config.neo4j_uri,
                neo4j_user=config.neo4j_user,
                neo4j_password=config.neo4j_password,
                llm_client=config.build_llm_client(),
            )

    def capabilities(self) -> BackendCapabilities:
        if not self._is_healthy():
            return BackendCapabilities()
        if self._config.deployment == GraphitiDeployment.SELF_HOSTED:
            # Self-hosted has Neo4j underneath — full traversal available.
            return BackendCapabilities(
                graph_traversal=True,
                vector_search=True,
                full_text_search=True,
            )
        else:
            # Hosted (Zep Cloud) does not expose raw Cypher; traversal stays Python-side.
            return BackendCapabilities(
                graph_traversal=False,
                vector_search=True,
                full_text_search=True,
            )

    def search_candidates(self, *, query_text, query_vec=None, filters=None, limit=24):
        # Self-hosted: self._client.search(query=query_text, num_results=limit)
        # Hosted:      self._client.memory.search_sessions(text=query_text, ...)
        # Both paths map results to {"bead_id": str, "score": float, ...} shape.
        # Bead ID resolution: episodes are stored with name=bead_id.
        ...

    def traverse(self, seed_ids, edge_types, max_hops, max_chains=5):
        # Self-hosted only (hosted raises NotImplementedError).
        # Runs constrained Cypher via Graphiti's underlying Neo4j driver.
        # Uses :Bead label namespace — same query as Neo4jGraphBackend.traverse()
        # but through Graphiti's driver rather than a standalone Neo4jClient.
        ...

    def on_bead_written(self, bead):
        # Two-mode write (both deployments):
        # Mode B (default, "explicit"): MERGE :Bead node with our schema.
        #   Self-hosted: Cypher via Graphiti driver.
        #   Hosted: add_episode with structured metadata; skip LLM extractor.
        # Mode A ("extracted"): full add_episode with LLM extraction enabled.
        #   Graphiti builds its own entity/relationship graph from bead text.
        #   Higher value for entity resolution; higher cost (LLM call per bead).
        # Config: CORE_MEMORY_GRAPHITI_MODE = explicit | extracted | both
        ...

    def on_association_written(self, assoc):
        # Mode B: MERGE typed relationship between :Bead nodes (self-hosted).
        # Mode B hosted: add structured episode describing the link.
        # Mode A: associations emerge implicitly from LLM extraction; no explicit write.
        ...

    def sync_from_storage(self, storage, *, dry_run=False):
        # Iterate all beads → on_bead_written. Then all associations → on_association_written.
        # Idempotent via MERGE / episode name dedup.
        ...
```

**Trade-offs to surface in docs:**

- **Mode B (explicit schema, default):** core-memory controls the ontology. No LLM
  calls on write. Traversal in self-hosted mode is equivalent to Neo4j direct. Choose
  this when you want predictable causal graph fidelity.
- **Mode A (LLM extraction, opt-in):** Graphiti builds a richer entity graph from bead
  text — useful for entity resolution across beads, temporal reasoning, and natural-
  language entity queries. Cost: one LLM call per bead write. Choose this when entity-
  level retrieval matters more than schema control.
- **Hosted vs. self-hosted:** hosted (Zep Cloud) requires no infrastructure but loses
  raw Cypher access and thus `graph_traversal=False`. Self-hosted gives you full
  capabilities at the cost of running Neo4j.

Both modes coexist on the same instance (different node label prefixes). The PRD must
make the LLM cost explicit: document it in the `GraphitiConfig.from_env()` docstring
and surface a `CORE_MEMORY_GRAPHITI_MODE` warning in the `core-memory doctor` command
(Phase 8).

The `"zep"` provider name in `CORE_MEMORY_GRAPH_BACKEND` is registered as a factory
alias to `GraphitiGraphBackend` with `deployment=HOSTED`. This lets users who think in
terms of "Zep" still use their familiar name without a separate class.

### Obsidian sync target

Obsidian is a markdown vault with a wikilink-based graph view. It is not a query engine
in the Neo4j/Graphiti sense, but it fills a different role: human-browsable memory,
AI-assisted synthesis via Smart Connections / Copilot plugins, and offline-capable
personal knowledge graph. Many power users already run Obsidian; surfacing beads there
is high value with low implementation complexity.

The shipped surface is `ObsidianSyncTarget` under
`core_memory.integrations.obsidian`, implementing the separate `BeadSyncTarget`
protocol. It is intentionally not a `GraphBackend`: Obsidian does not expose a
stable programmatic graph-traversal API.

```python
# core_memory/integrations/obsidian/vault.py
class ObsidianSyncTarget:
    name = "obsidian"

    def on_bead_written(self, bead):
        # Write {bead_id}.md to self._vault / self._subfolder /
        # File format:
        #   ---
        #   id: {bead_id}
        #   type: {bead.type}
        #   session_id: {bead.session_id}
        #   created_at: {bead.created_at}
        #   tags: [{bead.topics}]
        #   retrieval_eligible: {bead.retrieval_eligible}
        #   ---
        #   # {bead.title}
        #
        #   {bead.summary}
        #
        #   {bead.detail}
        #
        #   ## Because
        #   {bead.because}
        #
        #   ## Links
        #   (populated later by on_association_written)
        ...

    def on_association_written(self, assoc):
        # Update the source bead's .md file: append [[target_bead_id]] under ## Links.
        # Use frontmatter field `associations:` for machine-readable access.
        # Idempotent: check if link already present before appending.
        ...

    def on_bead_retracted(self, bead_id):
        # Append a "retracted" callout block to the .md file.
        # Do NOT delete the file — retraction is part of the audit trail.
        ...

    def sync_from_storage(self, beads, associations):
        # Iterate all beads → on_bead_written. Then all associations → on_association_written.
        # Idempotent: overwrite existing files (content-idempotent via deterministic format).
        ...
```

**Obsidian Local REST API plugin** ([obsidian-local-rest-api](https://github.com/coddingtonbear/obsidian-local-rest-api)):
enables `GET /search/simple/`, `GET /vault/{filename}`, `PUT /vault/{filename}`.
This is the only mechanism for programmatic read-back; it requires the plugin to be
installed and running. If the plugin is absent, `ObsidianSyncTarget` is write-only
(still useful for the human-browsable graph).

**File naming and collision avoidance:** bead IDs are UUIDs; filenames are
`{bead_id}.md`. No collision possible. The subfolder default is `core-memory/` inside
the vault root. Users can redirect to any existing subfolder via
`CORE_MEMORY_OBSIDIAN_SUBFOLDER`.

**Why not `graph_traversal=True`?** Wikilinks are bidirectional in Obsidian's UI but
there is no public programmatic API to walk them hop-by-hop. Future Obsidian DataView /
DataCore APIs may expose this; we leave the capability False for now and revisit when
a stable API exists.

**Why not `vector_search=True`?** Smart Connections (the Obsidian plugin that does
vector search) is UI-only. There is no REST API for it. If/when that changes, a future
sub-phase adds the capability.

---

## Provider registry and factory

```python
# core_memory/persistence/graph/factory.py
import os
from pathlib import Path
from typing import Callable

from .protocol import GraphBackend
from .protocol import NullGraphBackend

_PROVIDERS: dict[str, Callable[[], GraphBackend]] = {
    "none": NullGraphBackend,
    "null": NullGraphBackend,
}


def register_graph_backend(name: str, factory: Callable[[], GraphBackend]) -> None:
    """Plugin hook for custom providers. Idempotent re-registration is allowed."""
    _PROVIDERS[name.strip().lower()] = factory


def create_graph_backend() -> GraphBackend:
    name = (os.environ.get("CORE_MEMORY_GRAPH_BACKEND") or "kuzu").strip().lower()
    factory = _PROVIDERS.get(name)
    if factory is not None:
        try:
            return factory()
        except Exception:
            return NullGraphBackend()

    if name == "kuzu":
        from .kuzu_backend import KuzuGraphBackend
        return KuzuGraphBackend(Path(".beads/kuzu"))

    if name == "neo4j":
        from .neo4j_backend import Neo4jGraphBackend
        return Neo4jGraphBackend.from_env()

    if name in {"graphiti", "zep"}:
        from .graphiti_backend import GraphitiGraphBackend
        deployment = "hosted" if name == "zep" else "local"
        return GraphitiGraphBackend.from_env(deployment=deployment)

    return NullGraphBackend()
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
$ CORE_MEMORY_GRAPH_BACKEND=neo4j core-memory graph backend-sync --root=. [--dry-run]
```

```python
# core_memory/cli/handlers/graph_sync.py
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
# Provider selection (default: kuzu → embedded graph backend, zero server ops)
# "zep" is an alias for graphiti with deployment=hosted
CORE_MEMORY_GRAPH_BACKEND = kuzu | none | neo4j | graphiti | zep | <custom>

# Neo4j (used by neo4j backend; also reused by graphiti in self-hosted mode)
CORE_MEMORY_NEO4J_URI        = bolt://localhost:7687
CORE_MEMORY_NEO4J_USER       = neo4j
CORE_MEMORY_NEO4J_PASSWORD   = ...
CORE_MEMORY_NEO4J_TLS        = 0|1
CORE_MEMORY_NEO4J_TIMEOUT_MS = 5000

# Graphiti / Zep — shared config section, deployment field selects transport
CORE_MEMORY_GRAPHITI_DEPLOYMENT  = self_hosted | hosted          # default: self_hosted
                                                                  # "hosted" = Zep Cloud
CORE_MEMORY_GRAPHITI_MODE        = explicit | extracted | both   # default: explicit
CORE_MEMORY_GRAPHITI_LLM_PROVIDER = anthropic | openai           # for Mode A extraction
CORE_MEMORY_GRAPHITI_LLM_MODEL   = claude-haiku-4-5-20251001
# Hosted (Zep Cloud) transport — only needed when GRAPHITI_DEPLOYMENT=hosted
CORE_MEMORY_GRAPHITI_API_KEY     = ...
CORE_MEMORY_GRAPHITI_BASE_URL    = https://api.getzep.com
CORE_MEMORY_GRAPHITI_SESSION_ID  = core-memory-default

# Obsidian
CORE_MEMORY_OBSIDIAN_VAULT_PATH  = /Users/you/Documents/MyVault
CORE_MEMORY_OBSIDIAN_SUBFOLDER   = core-memory        # default
CORE_MEMORY_OBSIDIAN_REST_URL    = http://127.0.0.1:27123  # Local REST API plugin; omit if not installed
CORE_MEMORY_OBSIDIAN_REST_KEY    = ...                # REST plugin API key (if configured)

# Health probe cadence (shared across all providers)
CORE_MEMORY_GRAPH_HEALTH_TTL_S   = 60
```

`Neo4jConfig`, `GraphitiConfig` (covers both self-hosted and hosted), and
`ObsidianConfig` dataclasses live next to their respective provider modules. Each
provides a `from_env()` classmethod used by the factory functions. There is no
separate `ZepConfig` — Zep configuration is a sub-set of `GraphitiConfig` with
`deployment=HOSTED`.

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
- `tests/test_graphiti_graph_backend_fake.py` — covers both deployments in one file.
  Self-hosted path: `graphiti_core.Graphiti` mocked; verify episode payloads (Mode A)
  and explicit-schema writes (Mode B). Hosted path: `zep_cloud.AsyncZep` mocked; verify
  same write/search calls go through Zep transport. Assert that `name == "graphiti"` for
  both, and that `capabilities().graph_traversal` is True for self-hosted, False for hosted.
- `tests/test_obsidian_graph_backend_fake.py` — `ObsidianConfig` pointed at a temp
  directory; verify `.md` file contents (frontmatter fields, wikilink format, retraction
  callout). REST API path: mock `httpx` / `requests` call to `/search/simple/`.

### Provider tests with live services (gated)

- `tests/test_neo4j_graph_backend_live.py` — `@pytest.mark.neo4j`, requires
  `CORE_MEMORY_NEO4J_URI` env. Docker Compose file at `tests/docker-compose.neo4j.yml`.
  Covers: bulk sync, 1-hop, 3-hop, edge-type filter, empty graph, disconnected seed,
  reachability outage mid-test.
- `tests/test_graphiti_graph_backend_live_selfhosted.py` — `@pytest.mark.graphiti`,
  requires `CORE_MEMORY_NEO4J_URI` + `CORE_MEMORY_GRAPHITI_LLM_API_KEY`. Smoke test
  for episode ingest + retrieval. Docker Compose Neo4j reused from neo4j-live job.
- `tests/test_graphiti_graph_backend_live_hosted.py` — `@pytest.mark.zep`, requires
  `CORE_MEMORY_GRAPHITI_API_KEY` + `CORE_MEMORY_GRAPHITI_DEPLOYMENT=hosted`. Hits Zep
  Cloud sandbox. Cleaned up via session deletion after each test.
- `tests/test_obsidian_graph_backend_live.py` — `@pytest.mark.obsidian`, requires
  `CORE_MEMORY_OBSIDIAN_VAULT_PATH` pointing to a real vault. Tests write + verify file
  contents. REST path requires `CORE_MEMORY_OBSIDIAN_REST_URL` with the plugin running.

### Pipeline integration tests

- `tests/test_retrieval_uses_graph_backend.py` — patches `create_graph_backend` to
  return a fake `Neo4jGraphBackend`, runs `trace_request`, asserts `traverse()` was
  called instead of `causal_traverse`, asserts result shape unchanged for callers.
- `tests/test_retrieval_falls_back_on_graph_failure.py` — fake backend returns
  `{"ok": False}` from `traverse`; pipeline falls back to Python and surfaces the
  expected warning.

### CI matrix

| Job                    | Providers tested        | Marker                    | Required env                              |
|------------------------|-------------------------|---------------------------|-------------------------------------------|
| default                | null                    | (unmarked)                | none                                      |
| neo4j-live             | neo4j                   | `@pytest.mark.neo4j`      | Docker Compose Neo4j 5                    |
| graphiti-selfhosted    | graphiti (self-hosted)  | `@pytest.mark.graphiti`   | Neo4j + LLM API key                       |
| graphiti-hosted (zep)  | graphiti (hosted)       | `@pytest.mark.zep`        | `CORE_MEMORY_GRAPHITI_API_KEY` + hosted   |
| obsidian-live          | obsidian                | `@pytest.mark.obsidian`   | `CORE_MEMORY_OBSIDIAN_VAULT_PATH`         |

The default CI job blocks merges and runs everywhere. The live jobs run on push to
main and on PRs that touch `persistence/graph/` or the corresponding integration
modules.

---

## Implementation phasing

| Sub-phase | Deliverable                                                                        | Gate            |
|-----------|------------------------------------------------------------------------------------|-----------------|
| **7a**    | `persistence/graph/` package skeleton: `GraphBackend` protocol + `NullGraphBackend` + factory (`create_graph_backend`) + retrieval pipeline wiring. | Phase 6 done |
| **7b**    | `Neo4jGraphBackend.traverse()` + `health()` + `capabilities()`. Read-side only. Reuses existing `Neo4jClient`. Live tests gated behind `@pytest.mark.neo4j`. | 7a |
| **7c**    | Write-side hook (`on_bead_written`, `on_association_written`) on `Neo4jGraphBackend`. Migrates `sync_to_neo4j` in-pipeline callers to the hook. | 7b |
| **7d**    | `core-memory graph backend-sync` CLI. Bulk backfill from local storage. Idempotent. | 7c |
| **7e**    | `GraphitiGraphBackend` — Mode B (explicit schema, no LLM). Self-hosted deployment. `traverse()` via Graphiti's Neo4j driver, `search_candidates` via `graphiti.search()`. | 7c |
| **7f**    | `GraphitiGraphBackend` — hosted deployment (Zep Cloud). Registered as `"zep"` alias. Same class, `CORE_MEMORY_GRAPHITI_DEPLOYMENT=hosted` switches transport to Zep HTTPS. | 7e |
| **7g**    | `GraphitiGraphBackend` Mode A — LLM extraction (`add_episode`). Behind `CORE_MEMORY_GRAPHITI_MODE=extracted`. Documents cost. | 7f |
| **7h**    | `ObsidianSyncTarget` — write `.md` files + wikilinks. Read via Local REST API if present. | 7d |
| **7i**    | Document the plugin API for third-party providers (`register_graph_backend`). | 7h |

Each sub-phase is independently shippable. 7a introduces the fallback provider and
factory. 7b makes Neo4j production-useful for traversal. 7c and 7d close the write
loop. 7e–7g deliver Graphiti (self-hosted then hosted then LLM mode). 7h adds
Obsidian as a sync target. 7i is documentation only.

---

## Backwards compatibility and migration

- **No env var set → embedded graph.** `KuzuGraphBackend` is the default. Existing
  users can set `CORE_MEMORY_GRAPH_BACKEND=none` to force `NullGraphBackend`.
  No server dependency is required at install time.
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
| **Graphiti LLM-extraction cost.** Every bead write triggers LLM calls in Mode A. | Mode A is opt-in. Default is Mode B (explicit schema, no LLM). Cost documented in `GraphitiConfig.from_env()` docstring; surfaced by `core-memory doctor`. |
| **Schema drift between providers.** Neo4j `:Bead` labels and Graphiti's auto-extracted nodes coexist in the same Neo4j instance; queries might cross-pollinate. | Namespace all our writes under a fixed label set (`:Bead`, `:Association`). Document that Graphiti's auto-extracted graph is a side index with its own label prefixes. |
| **Traversal result shape mismatch.** Provider returns chains with subtly different fields than Python `causal_traverse_chains`. | Define `GraphTraversalResult` TypedDict in `persistence/graph/types.py`. Every provider runs `_normalize_traversal_result` before returning. Contract-tested in fake tests. |
| **Live tests flaky in CI.** Docker Compose Neo4j slow to boot; Zep Cloud sandbox rate-limited. | Live tests are gated and run async to the default suite. Default CI stays green on null backend only. Zep live tests use isolated session IDs cleaned up post-test. |
| **Provider connection holds linger.** Tests forget `close()`, leaving sockets open. | Factory returns a process-scope singleton; `conftest.py` registers `atexit` / session-scope fixture that calls `close()`. |
| **Graphiti hosted (Zep) session model doesn't fit core-memory's session_id.** | Map `CORE_MEMORY_GRAPHITI_SESSION_ID` env to the Zep session identifier. Core-memory `session_id` becomes a metadata field on each episode. Document the mismatch explicitly. |
| **Obsidian vault path on CI.** CI runners don't have an Obsidian vault. | Obsidian live tests are `@pytest.mark.obsidian` and skipped unless `CORE_MEMORY_OBSIDIAN_VAULT_PATH` is set. Fake tests use `tempfile.TemporaryDirectory` and don't need a real vault. |
| **Obsidian file conflicts.** Two processes writing to the same vault simultaneously. | `.md` writes are atomic (write-to-temp then `os.replace`). Same pattern as `atomic_write_json` in `io_utils.py`. |

---

## Out of scope (deferred to later phases)

- **Memgraph, Kuzu, FalkorDB, JanusGraph, AWS Neptune adapters.** The plugin API in
  7i is enough; third parties can ship these out-of-tree.
- **Obsidian `graph_traversal=True`.** No public Obsidian API for programmatic
  wikilink traversal today. Revisit when DataCore or a community plugin exposes one.
- **Obsidian Smart Connections integration (`vector_search=True`).** Smart Connections
  is UI-only. No REST API. Out of scope until an API exists.
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
   if write latency becomes a problem in production.
2. **How does rolling-window compaction interact with graph backends?** When a bead is
   archived or compressed, should the graph backend retain it? Default: yes (the graph
   is an append-only audit log); revisit if Zep Cloud storage costs become a concern.
3. **Provider observability.** Do we add a `GraphBackend.metrics()` method for per-
   provider operation counts / latency histograms? Phase 7 punts; structured logs are
   sufficient initially.
4. **Obsidian bidirectional sync.** If a user edits a bead's `.md` file in Obsidian,
   should Core Memory pick up that change? Phase 7 is write-only from Core Memory's
   perspective. Bidirectional sync (vault → bead store) is a significant scope increase
   and requires a separate PRD.
5. **Graphiti Mode A + Obsidian.** Can we run both simultaneously — Graphiti for
   entity-level retrieval and Obsidian for human browsing — without a multi-provider
   routing mechanism? Yes: the user runs two graph backends via separate env setups (one
   process per provider). The unified routing is deferred to the multi-provider phase.
