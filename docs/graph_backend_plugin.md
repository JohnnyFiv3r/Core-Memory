# Graph Backend Plugin API

**Status:** Canonical reference for `persistence/graph/` plugin extension points.

Core Memory's graph tier is pluggable. Any process can register a custom `GraphBackend`
implementation without touching the Core Memory source code.

---

## The `GraphBackend` protocol

All graph backends implement `core_memory.persistence.graph.protocol.GraphBackend`:

```python
class GraphBackend(Protocol):
    name: str  # identifier string used in logs and factory resolution

    def capabilities(self) -> BackendCapabilities: ...
    def health(self) -> dict: ...

    # write hooks — called after every local bead/association write
    def on_bead_written(self, bead: dict) -> None: ...
    def on_association_written(self, assoc: dict) -> None: ...
    def on_bead_retracted(self, bead_id: str) -> None: ...

    # read (optional — return empty results if not supported)
    def traverse(
        self, seed_ids: list[str], edge_types: list[str] | None, max_hops: int
    ) -> list[dict]: ...
    def search_candidates(
        self, query_text: str, k: int = 8, filters: dict | None = None
    ) -> dict: ...

    # bulk sync — called by `core-memory graph backend-sync`
    def sync_from_storage(
        self, beads: list[dict], associations: list[dict]
    ) -> dict: ...

    def close(self) -> None: ...
```

`BackendCapabilities` is a dataclass with boolean flags:

```python
@dataclass
class BackendCapabilities:
    graph_traversal: bool = False
    vector_search: bool = False
    full_text_search: bool = False
    transcript_hydration: bool = False
```

---

## Built-in backends

| `CORE_MEMORY_GRAPH_BACKEND` value | Class | Notes |
|---|---|---|
| `none` / `""` | `NullGraphBackend` | Default; no-op write hooks |
| `kuzu` | `KuzuGraphBackend` | Embedded graph DB; zero external deps |
| `neo4j` | `Neo4jGraphBackend` | Requires `core-memory[neo4j]` + Neo4j server |
| `graphiti` | `GraphitiGraphBackend` | Temporal KG; requires `core-memory[graphiti]` |
| `zep` | `GraphitiGraphBackend(deployment="hosted")` | Zep-hosted Graphiti; same extra |

---

## Registering a custom backend

Use `register_graph_backend` to add a provider without forking the factory:

```python
from core_memory.persistence.graph.factory import register_graph_backend

class MyGraphBackend:
    name = "my-backend"

    def capabilities(self):
        from core_memory.persistence.graph.protocol import BackendCapabilities
        return BackendCapabilities(graph_traversal=True)

    def health(self):
        return {"ok": True, "backend": self.name}

    def on_bead_written(self, bead): ...
    def on_association_written(self, assoc): ...
    def on_bead_retracted(self, bead_id): ...
    def traverse(self, seed_ids, edge_types, max_hops): return []
    def search_candidates(self, query_text, k=8, filters=None):
        return {"ok": False, "results": [], "warnings": ["not implemented"]}
    def sync_from_storage(self, beads, associations):
        return {"synced_beads": 0, "synced_associations": 0, "errors": []}
    def close(self): pass

register_graph_backend("my-backend", lambda root: MyGraphBackend())
```

Then activate it:

```bash
export CORE_MEMORY_GRAPH_BACKEND=my-backend
```

The second argument to `register_graph_backend` is a factory callable that receives the
project root `Path` and returns an instance.

---

## Graphiti backend

`GraphitiGraphBackend` wraps [graphiti-core](https://github.com/getzep/graphiti) to provide
temporal knowledge graph capabilities.

### Installation

```bash
pip install core-memory[graphiti]
```

### Activation

```bash
export CORE_MEMORY_GRAPH_BACKEND=graphiti
export CORE_MEMORY_NEO4J_URI=bolt://localhost:7687
export CORE_MEMORY_NEO4J_USER=neo4j
export CORE_MEMORY_NEO4J_PASSWORD=yourpassword
```

### LLM client injection

Graphiti requires an LLM client for episode extraction. The factory always passes
`llm_client=None` — users inject it after construction:

```python
from core_memory.persistence.graph.factory import register_graph_backend
from core_memory.persistence.graph.graphiti_backend import GraphitiGraphBackend
from my_llm import MyLLMClient

register_graph_backend(
    "graphiti",
    lambda root: GraphitiGraphBackend.from_env(llm_client=MyLLMClient()),
)
```

The `llm_client` must implement `GraphitiLLMClientProtocol`:

```python
class GraphitiLLMClientProtocol(Protocol):
    async def generate_response(self, messages: list[dict], **kwargs) -> str: ...
```

### Zep-hosted alias

To use Graphiti on Zep's hosted cloud:

```bash
export CORE_MEMORY_GRAPH_BACKEND=zep
export CORE_MEMORY_NEO4J_URI=bolt://your-zep-endpoint:7687
export CORE_MEMORY_ZEP_API_KEY=your-api-key
```

This resolves to `GraphitiGraphBackend(deployment="hosted")` which uses Zep's FalkorDB
driver instead of a local Neo4j instance.

---

## Obsidian sync target

Obsidian integration uses a separate `BeadSyncTarget` protocol — not `GraphBackend` — because
Obsidian is a write-only mirror (no traversal).

### Installation

```bash
pip install core-memory[obsidian]
```

### Activation

```bash
export CORE_MEMORY_SYNC_TARGETS=obsidian
export CORE_MEMORY_OBSIDIAN_VAULT=/path/to/your/vault
# optional — enables search via Obsidian Local REST API plugin:
export CORE_MEMORY_OBSIDIAN_REST_URL=http://localhost:27123
```

Every bead write creates `{vault}/{session_id}/{bead_id}.md` with YAML frontmatter and
wikilink associations. Retracted beads get `status: retracted` in frontmatter.

### Custom sync targets

```python
from core_memory.integrations.obsidian.protocol import BeadSyncTarget
from core_memory.persistence.graph.factory import register_graph_backend  # not used here

class MyMirror:
    name = "my-mirror"
    def on_bead_written(self, bead): ...
    def on_association_written(self, assoc): ...
    def on_bead_retracted(self, bead_id): ...
    def sync_from_storage(self, beads, associations): return {"synced_beads": 0, "synced_associations": 0, "errors": []}
    def close(self): pass
```

Register it in `CORE_MEMORY_SYNC_TARGETS` by extending `_create_sync_targets()` in
the sync-target persistence helper, or open an issue to add it as a
first-class target.

---

## Write-path wiring

Graph backend and sync target calls happen automatically after every local write:

```
bead write (JSON/SQLite)
  └─ core_memory.runtime.post_write.bead_commit._mirror_bead_to_backends()
       ├─ create_graph_backend().on_bead_written(bead)    # graph tier
       └─ for st in _create_sync_targets():               # sync targets
            st.on_bead_written(bead)
```

Failures are logged as warnings and never block the local write. The graph backend is
also callable via:

```bash
core-memory graph backend-sync [--dry-run]
```

This bulk-loads all beads and associations from storage into the configured graph backend.

### Staleness and the active-association view

Backends keep their own edge copies and can lag the canonical index (e.g. a
retraction edits `index.json` between syncs). This is a **freshness** concern,
never a correctness one: the canonical trace consumer applies the
active-association view to all backend chains
(`graph.traversal.filter_chains_to_active_edges`), truncating each chain at
the first edge with no active association in the index. A lagging backend can
therefore never surface retracted/superseded edges through retrieval — but
schedule `backend-sync` after bulk ingests if a visualization reads the
backend directly.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CORE_MEMORY_GRAPH_BACKEND` | `kuzu` | Active graph backend provider name |
| `CORE_MEMORY_NEO4J_URI` | `bolt://localhost:7687` | Neo4j / Graphiti connection URI |
| `CORE_MEMORY_NEO4J_USER` | `neo4j` | Neo4j username |
| `CORE_MEMORY_NEO4J_PASSWORD` | `""` | Neo4j password |
| `CORE_MEMORY_ZEP_API_KEY` | `""` | Zep API key (for `zep` backend) |
| `CORE_MEMORY_SYNC_TARGETS` | `""` | Comma-separated sync target names (e.g. `obsidian`) |
| `CORE_MEMORY_OBSIDIAN_VAULT` | `""` | Path to Obsidian vault root |
| `CORE_MEMORY_OBSIDIAN_REST_URL` | `""` | Obsidian Local REST API URL |
