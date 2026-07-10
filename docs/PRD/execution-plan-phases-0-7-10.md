# Execution Plan: Phases 0, 7 (remaining), 10

**Status:** Complete — Graphiti/Zep, Obsidian, live Neo4j gating, and Phase 10 docs shipped
**Historical branch:** `claude/validate-demo-todos-SCRSz` (or a new branch cut from it)
**Prerequisite:** Phases 1–9 complete (confirmed). Phase 6 `BackendCapabilities` in place.
**Scope:** Phase 0 cleanup, Phase 7 sub-phases 7e–7i + 7b live tests, Phase 10 10a–10g.

---

## Current implementation note

This one-pass execution plan is complete in the current tree. `docs/status.md`
marks Phase 7e–7i and Phase 10a–10g done, and `docs/PRD/README.md` indexes this
plan as done. The shipped surface includes `GraphitiGraphBackend`, the Zep alias,
LLM-client injection guidance in `docs/graph_backend_plugin.md`,
`ObsidianSyncTarget`, the graph backend plugin docs, and the workflow-dispatched
Neo4j live test job.

The sections below are retained as historical implementation context. Embedded
code snippets and "new file" labels describe what the original plan proposed,
not open work remaining today.

---

## Historical dependency order

```
Step A  — side_effect_queue: add graphiti-episode-add job kind
Step B  — GraphitiGraphBackend (7e)
Step C  — Zep alias on GraphitiGraphBackend (7f)
Step D  — LLM client injection docs / no-op default (7g)
Step E  — BeadSyncTarget protocol + ObsidianSyncTarget (7h)
Step F  — plugin API docs: docs/graph_backend_plugin.md (7i)
Step G  — pyproject.toml: new markers + graphiti extra
Step H  — Neo4j live test file + workflow_dispatch CI job (7b remainder)
Step I  — 10a: archive v2_p* files
Step J  — 10b: retire docs/ARCHITECTURE.md
Step K  — 10c: update architecture_overview.md (requires J)
Step L  — 10d: classify docs root + update semantic_backend_modes.md
Step M  — 10e: create docs/status.md; align CLAUDE.md
Step N  — 10f: docs/PRD/README.md
Step O  — 10g: update docs/index.md (requires I, J, K, L)
Step P  — Phase 0: check boxes in cleanup-plan.md (last; all work already done)
```

---

## Step A — Add `graphiti-episode-add` job kind to side_effect_queue

**File:** `core_memory/runtime/queue/side_effect_queue.py`

Change:
```python
_SIDE_EFFECT_KINDS = {"dreamer-run", "neo4j-sync", "health-recompute", "turn-enrichment"}
```
To:
```python
_SIDE_EFFECT_KINDS = {
    "dreamer-run", "neo4j-sync", "health-recompute",
    "turn-enrichment", "graphiti-episode-add",
}
```

In `_process_one`, add a dispatch block after the `neo4j-sync` block (around line 241):
```python
if k == "graphiti-episode-add":
    try:
        from core_memory.persistence.graph.factory import create_graph_backend
        from core_memory.persistence.graph.graphiti_backend import GraphitiGraphBackend
        gb = create_graph_backend(Path(root))
        if not isinstance(gb, GraphitiGraphBackend):
            return {"ok": True, "kind": k, "terminal_skipped": True,
                    "reason": "active backend is not GraphitiGraphBackend"}
        bead = p.get("bead") or {}
        assoc = p.get("assoc")
        if assoc:
            gb._write_association_sync(assoc)
        else:
            gb._write_bead_sync(bead)
        return {"ok": True, "kind": k}
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("graphiti-episode-add failed: %s", exc)
        return {"ok": False, "kind": k, "error": {"code": "graphiti_write_error", "detail": str(exc)}}
```

**File:** `core_memory/runtime/queue/side_effects.py`

Add `"graphiti-episode-add"` to the enabled-kinds default string and add an enqueue block
parallel to the `neo4j-sync` block:
```python
if "graphiti-episode-add" in enabled or "graphiti" in enabled:
    # Graphiti writes are enqueued per bead; flushed in bulk here
    out["enqueued"]["graphiti-sync"] = enqueue_side_effect_event(
        root=root,
        kind="graphiti-episode-add",
        payload={"session_id": session_id, "bulk_sync": True},
        idempotency_key=f"graphiti:{session_id}:{flush_tx_id}",
    )
```

---

## Step B — `GraphitiGraphBackend` (Phase 7e)

**New file:** `core_memory/persistence/graph/graphiti_backend.py`

```python
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Protocol, runtime_checkable

from core_memory.persistence.backend import BackendCapabilities

_log = logging.getLogger(__name__)
_DEFAULT_HEALTH_TTL_S = 60.0


@runtime_checkable
class GraphitiLLMClientProtocol(Protocol):
    """Minimal interface Graphiti expects from an LLM client.

    Callers that want LLM-augmented edge extraction should pass a concrete
    graphiti_core.llm_client.* instance. Passing None disables LLM extraction.
    The persistence layer never constructs this object — injection only.
    """
    async def generate_response(self, messages: list[dict[str, Any]], **kwargs: Any) -> str: ...


class GraphitiGraphBackend:
    """GraphitiGraphBackend wraps graphiti-core for temporal KG storage.

    Write hooks (on_bead_written, on_association_written) are fire-and-forget:
    they enqueue a side_effect_queue job rather than calling asyncio.run() inline.
    Read methods (traverse, search_candidates) run via a persistent background
    event loop managed by _get_loop().

    LLM client injection: the factory always passes llm_client=None (no LLM
    extraction). Pass a GraphitiLLMClientProtocol instance to enable semantic
    edge inference — the persistence layer never constructs one itself.
    """

    name = "graphiti"

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        deployment: str = "local",
        zep_api_key: str | None = None,
        llm_client: GraphitiLLMClientProtocol | None = None,
    ) -> None:
        try:
            from graphiti_core import Graphiti
        except ImportError:
            raise ImportError(
                "Graphiti backend requires: pip install core-memory[graphiti]"
            )
        if deployment == "hosted":
            if not zep_api_key:
                raise ValueError("ZEP_API_KEY required for deployment='hosted'")
            from graphiti_core.driver.falkordb import FalkorDBDriver  # Zep transport
            driver = FalkorDBDriver(url=uri, api_key=zep_api_key)
        else:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(uri, auth=(user, password))

        self._client = Graphiti(driver=driver, llm_client=llm_client)
        # Initialise indices once; non-fatal if already exist
        try:
            asyncio.run(self._client.build_indices_and_constraints())
        except Exception as exc:
            _log.warning("graphiti index setup warning: %s", exc)

        self._healthy: bool | None = None
        self._health_checked_at: float = 0.0
        self._health_ttl_s = float(
            os.environ.get("CORE_MEMORY_GRAPH_HEALTH_TTL_S") or _DEFAULT_HEALTH_TTL_S
        )
        # Background event loop for read-path coroutines
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_env(cls, *, deployment: str = "local") -> "GraphitiGraphBackend":
        return cls(
            uri=os.environ.get("CORE_MEMORY_NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("CORE_MEMORY_NEO4J_USER", "neo4j"),
            password=os.environ.get("CORE_MEMORY_NEO4J_PASSWORD", ""),
            deployment=deployment,
            zep_api_key=os.environ.get("ZEP_API_KEY"),
            llm_client=None,  # never constructed here; injected by caller if needed
        )

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Return a persistent background event loop for read coroutines."""
        import threading
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            t = threading.Thread(target=self._loop.run_forever, daemon=True)
            t.start()
        return self._loop

    def _run(self, coro: Any) -> Any:
        """Run a coroutine on the background loop; block until done."""
        import concurrent.futures
        fut = asyncio.run_coroutine_threadsafe(coro, self._get_loop())
        return fut.result(timeout=30)

    def capabilities(self) -> BackendCapabilities:
        if self._healthy is None or (time.monotonic() - self._health_checked_at) >= self._health_ttl_s:
            result = self.health()
            self._healthy = bool(result.get("ok"))
            self._health_checked_at = time.monotonic()
        if not self._healthy:
            return BackendCapabilities()
        return BackendCapabilities(graph_traversal=True, vector_search=True)

    def health(self) -> dict:
        try:
            self._run(self._client.driver.verify_connectivity())
            return {"ok": True, "backend": "graphiti"}
        except Exception as exc:
            return {"ok": False, "backend": "graphiti", "error": str(exc)}

    def on_bead_written(self, bead: dict) -> None:
        """Enqueue an async episode-add to side_effect_queue; never blocks."""
        bead_id = str(bead.get("id") or "")
        if not bead_id:
            return
        try:
            from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event
            from core_memory.persistence.store import DEFAULT_ROOT
            enqueue_side_effect_event(
                root=DEFAULT_ROOT,
                kind="graphiti-episode-add",
                payload={"bead": bead},
                idempotency_key=f"graphiti:bead:{bead_id}",
            )
        except Exception as exc:
            _log.warning("graphiti on_bead_written enqueue failed: %s", exc)

    def on_association_written(self, assoc: dict) -> None:
        """Enqueue association write; Graphiti handles relationship inference."""
        assoc_id = str(assoc.get("id") or "")
        if not assoc_id:
            return
        try:
            from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event
            from core_memory.persistence.store import DEFAULT_ROOT
            enqueue_side_effect_event(
                root=DEFAULT_ROOT,
                kind="graphiti-episode-add",
                payload={"assoc": assoc},
                idempotency_key=f"graphiti:assoc:{assoc_id}",
            )
        except Exception as exc:
            _log.warning("graphiti on_association_written enqueue failed: %s", exc)

    def _write_bead_sync(self, bead: dict) -> None:
        """Called by the side_effect_queue worker — runs the actual Graphiti call."""
        from graphiti_core.nodes import EpisodeType
        self._run(self._client.add_episode(
            name=str(bead.get("id") or ""),
            episode_body="\n".join(bead.get("summary") or [str(bead.get("title") or "")]),
            reference_time=str(bead.get("created_at") or ""),
            source=EpisodeType.text,
            source_description=str(bead.get("session_id") or ""),
            group_id=str(bead.get("type") or ""),
        ))

    def _write_association_sync(self, assoc: dict) -> None:
        """Graphiti infers relationships from episodes; this is a supplementary edge hint."""
        _log.debug(
            "graphiti association %s→%s noted (relationship inference via episode content)",
            assoc.get("source_bead"), assoc.get("target_bead"),
        )

    def on_bead_retracted(self, bead_id: str) -> None:
        try:
            self._run(self._client.delete_episode(str(bead_id)))
        except Exception as exc:
            _log.warning("graphiti on_bead_retracted failed: %s", exc)

    def traverse(
        self,
        seed_ids: list[str],
        edge_types: list[str] | None,
        max_hops: int,
        max_chains: int = 16,
    ) -> list[dict]:
        if not seed_ids:
            return []
        try:
            results = self._run(self._client.search(
                query=" ".join(seed_ids),
                num_results=max_chains,
            ))
            return [
                {"nodes": [{"id": r.uuid, "type": "graphiti_fact", "title": str(r.fact)}], "edges": []}
                for r in (results or [])
            ]
        except Exception as exc:
            _log.warning("graphiti traverse failed: %s", exc)
            return []

    def search_candidates(
        self,
        query_text: str,
        k: int = 8,
        filters: dict | None = None,
    ) -> dict:
        try:
            results = self._run(self._client.search(query=query_text, num_results=k))
            items = [
                {"bead_id": str(r.uuid), "score": float(getattr(r, "score", 1.0)),
                 "metadata": {"fact": str(r.fact)}}
                for r in (results or [])
            ]
            return {"ok": True, "results": items, "warnings": []}
        except Exception as exc:
            return {"ok": False, "results": [], "warnings": [str(exc)]}

    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict:
        bead_count = 0
        errors: list[str] = []
        for bead in beads:
            try:
                self._write_bead_sync(bead)
                bead_count += 1
            except Exception as exc:
                errors.append(f"bead:{bead.get('id')}:{exc}")
        return {"synced_beads": bead_count, "synced_associations": 0, "errors": errors}

    def close(self) -> None:
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
```

**Register in `factory.py`** — add after the `neo4j` block:
```python
if name == "graphiti":
    try:
        from .graphiti_backend import GraphitiGraphBackend
        return GraphitiGraphBackend.from_env(deployment="local")
    except Exception as exc:
        _log.warning("graphiti graph backend construction failed (%s); falling back to null", exc)
        return NullGraphBackend()

if name == "zep":
    try:
        from .graphiti_backend import GraphitiGraphBackend
        return GraphitiGraphBackend.from_env(deployment="hosted")
    except Exception as exc:
        _log.warning("zep graph backend construction failed (%s); falling back to null", exc)
        return NullGraphBackend()
```

---

## Step C — Zep alias (Phase 7f)

No additional files. `deployment="hosted"` is already handled in Step B's `from_env()`.
The factory entry for `"zep"` in Step B covers 7f completely.

---

## Step D — LLM client injection guidance (Phase 7g)

No code change in `persistence/`. The `GraphitiLLMClientProtocol` Protocol defined in
Step B is the contract. Document the injection pattern in Step F (plugin docs).

The key invariant: **`factory.py` always passes `llm_client=None`**.
Users who want LLM-augmented edge extraction call `register_graph_backend`:
```python
from graphiti_core.llm_client.openai_client import OpenAIClient
from core_memory.persistence.graph import register_graph_backend
from core_memory.persistence.graph.graphiti_backend import GraphitiGraphBackend

register_graph_backend(
    "graphiti",
    lambda: GraphitiGraphBackend.from_env(llm_client=OpenAIClient(config=...)),
)
```
This stays in user/integration space; `persistence/` never touches LLM libraries.

---

## Step E — `BeadSyncTarget` + `ObsidianSyncTarget` (Phase 7h)

### 5a — New protocol file

**New file:** `core_memory/integrations/obsidian/protocol.py`
```python
from __future__ import annotations
from typing import Protocol, runtime_checkable

@runtime_checkable
class BeadSyncTarget(Protocol):
    """Write-mirror protocol for outgoing sync targets (Obsidian, etc.).

    Distinct from GraphBackend: no traversal, no capabilities negotiation.
    Fire-and-forget write hooks only. Never block the local write path.
    """
    name: str

    def on_bead_written(self, bead: dict) -> None: ...
    def on_association_written(self, assoc: dict) -> None: ...
    def on_bead_retracted(self, bead_id: str) -> None: ...
    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict: ...
    def close(self) -> None: ...
```

### 5b — `ObsidianSyncTarget`

**New file:** `core_memory/integrations/obsidian/vault.py`
```python
from __future__ import annotations

import logging
import os
from pathlib import Path

from core_memory.persistence.io_utils import store_lock

_log = logging.getLogger(__name__)


class ObsidianSyncTarget:
    """Writes beads as Obsidian markdown files with YAML frontmatter and wikilinks.

    Read-side: optional Obsidian Local REST API for full-text search.
    Traversal: not supported (no programmatic graph query).
    """

    name = "obsidian"

    def __init__(self, vault_path: str, rest_api_url: str | None = None) -> None:
        self._vault = Path(vault_path) if vault_path else None
        self._rest_url = (rest_api_url or "").rstrip("/") or None
        if self._vault and not self._vault.exists():
            self._vault.mkdir(parents=True, exist_ok=True)
            _log.info("obsidian: created vault directory %s", self._vault)

    @classmethod
    def from_env(cls) -> "ObsidianSyncTarget":
        return cls(
            vault_path=os.environ.get("CORE_MEMORY_OBSIDIAN_VAULT", ""),
            rest_api_url=os.environ.get("CORE_MEMORY_OBSIDIAN_REST_URL"),
        )

    def _bead_path(self, bead: dict) -> Path | None:
        if not self._vault:
            return None
        session = str(bead.get("session_id") or "unsorted")
        bead_id = str(bead.get("id") or "")
        if not bead_id:
            return None
        return self._vault / session / f"{bead_id}.md"

    def _render_md(self, bead: dict) -> str:
        summary_lines = bead.get("summary") or []
        summary_md = "\n".join(f"- {line}" for line in summary_lines)
        return (
            f"---\n"
            f"id: {bead.get('id', '')}\n"
            f"type: {bead.get('type', '')}\n"
            f"title: {bead.get('title', '')}\n"
            f"status: {bead.get('status', 'open')}\n"
            f"session_id: {bead.get('session_id', '')}\n"
            f"created_at: {bead.get('created_at', '')}\n"
            f"---\n\n"
            f"# {bead.get('title', '')}\n\n"
            f"{summary_md}\n"
        )

    def on_bead_written(self, bead: dict) -> None:
        path = self._bead_path(bead)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with store_lock(path.parent):
                path.write_text(self._render_md(bead), encoding="utf-8")
        except Exception as exc:
            _log.warning("obsidian on_bead_written failed: %s", exc)

    def on_association_written(self, assoc: dict) -> None:
        """Append a wikilink to the source bead's .md file."""
        if not self._vault:
            return
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        if not (src and tgt):
            return
        # Find the source file across all session subdirs
        matches = list(self._vault.glob(f"**/{src}.md"))
        if not matches:
            return
        src_path = matches[0]
        try:
            with store_lock(src_path.parent):
                existing = src_path.read_text(encoding="utf-8")
                link = f"[[{tgt}]]"
                if link not in existing:
                    src_path.write_text(existing.rstrip() + f"\n\n{link}\n", encoding="utf-8")
        except Exception as exc:
            _log.warning("obsidian on_association_written failed: %s", exc)

    def on_bead_retracted(self, bead_id: str) -> None:
        if not self._vault:
            return
        matches = list(self._vault.glob(f"**/{bead_id}.md"))
        for path in matches:
            try:
                with store_lock(path.parent):
                    existing = path.read_text(encoding="utf-8")
                    # Update frontmatter status line
                    updated = existing.replace("status: open", "status: retracted", 1)
                    updated = updated.replace("status: candidate", "status: retracted", 1)
                    updated += "\n> **RETRACTED**\n"
                    path.write_text(updated, encoding="utf-8")
            except Exception as exc:
                _log.warning("obsidian on_bead_retracted failed: %s", exc)

    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict:
        bead_count = 0
        errors: list[str] = []
        for bead in beads:
            try:
                self.on_bead_written(bead)
                bead_count += 1
            except Exception as exc:
                errors.append(f"bead:{bead.get('id')}:{exc}")
        for assoc in associations:
            try:
                self.on_association_written(assoc)
            except Exception as exc:
                errors.append(f"assoc:{assoc.get('id')}:{exc}")
        return {"synced_beads": bead_count, "synced_associations": len(associations), "errors": errors}

    def search_candidates(self, query_text: str, k: int = 8, filters: dict | None = None) -> dict:
        """Proxy to Obsidian Local REST API if configured; otherwise returns empty."""
        if not self._rest_url:
            return {"ok": False, "results": [], "warnings": ["REST API not configured"]}
        try:
            import urllib.request, urllib.parse, json as _json
            url = f"{self._rest_url}/search/simple/?query={urllib.parse.quote(query_text)}&contextLength=100"
            with urllib.request.urlopen(url, timeout=5) as resp:  # nosec
                data = _json.loads(resp.read())
            results = [
                {"bead_id": Path(r.get("filename", "")).stem, "score": 1.0, "metadata": r}
                for r in (data if isinstance(data, list) else [])
            ][:k]
            return {"ok": True, "results": results, "warnings": []}
        except Exception as exc:
            return {"ok": False, "results": [], "warnings": [str(exc)]}

    def close(self) -> None:
        pass
```

**New file:** `core_memory/integrations/obsidian/__init__.py`
```python
from .protocol import BeadSyncTarget
from .vault import ObsidianSyncTarget

__all__ = ["BeadSyncTarget", "ObsidianSyncTarget"]
```

### 5c — Wire `BeadSyncTarget` into `MemoryStore`

**File:** `core_memory/persistence/store_init_ops.py`

Add after graph backend init:
```python
def _create_sync_targets(root: Path) -> list:
    """Instantiate configured sync targets from env. Returns empty list if none."""
    targets_env = (os.environ.get("CORE_MEMORY_SYNC_TARGETS") or "").strip().lower()
    if not targets_env or targets_env in ("none", ""):
        return []
    targets = []
    for name in [t.strip() for t in targets_env.split(",") if t.strip()]:
        if name == "obsidian":
            try:
                from core_memory.integrations.obsidian import ObsidianSyncTarget
                targets.append(ObsidianSyncTarget.from_env())
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("obsidian sync target init failed: %s", exc)
    return targets
```

**File:** `core_memory/persistence/store.py`

In `MemoryStore.__init__`, alongside `self._graph_backend`, add:
```python
self._sync_targets: list = _create_sync_targets(self._root)
```

**File:** `core_memory/persistence/store_add_bead_ops.py`

In `_mirror_bead_to_backends`, add iteration over sync targets:
```python
def _mirror_bead_to_backends(store: Any, bead: dict) -> None:
    gb = getattr(store, "_graph_backend", None)
    if gb is not None:
        try:
            gb.on_bead_written(bead)
        except Exception as exc:
            _log.warning("graph backend on_bead_written failed: %s", exc)
    for st in getattr(store, "_sync_targets", []):
        try:
            st.on_bead_written(bead)
        except Exception as exc:
            _log.warning("sync target %s on_bead_written failed: %s", getattr(st, "name", "?"), exc)
```

Apply the same pattern to `store_relationship_ops.py` for `on_association_written`.

### 5d — Test file

**New file:** `tests/test_obsidian_sync_target.py`
```python
import pytest
pytestmark = pytest.mark.obsidian

# Tests:
# 1. on_bead_written creates {vault}/{session_id}/{bead_id}.md with YAML frontmatter
# 2. on_association_written appends [[target_id]] wikilink to source .md
# 3. on_bead_retracted rewrites status line + appends RETRACTED notice
# 4. search_candidates returns {"ok": False} when no REST URL configured
# 5. sync_from_storage writes all beads then replays associations
# 6. vault_path="" → all methods no-op without raising
# Use tmp_path fixture; no mocking needed
```

---

## Step F — Plugin API docs (Phase 7i)

**New file:** `docs/graph_backend_plugin.md`

Content (60–80 lines):

```markdown
# Graph Backend Plugin API

## Register a custom provider

from core_memory.persistence.graph.factory import register_graph_backend
register_graph_backend("mygraph", lambda: MyGraphBackend.from_env())

Set CORE_MEMORY_GRAPH_BACKEND=mygraph. The factory calls your zero-arg callable
and returns a NullGraphBackend on any construction failure.

## GraphBackend protocol

Implement all methods in core_memory/persistence/graph/protocol.py:

| Method | Called when | Must not raise |
|--------|-------------|----------------|
| capabilities() | Every retrieval decision | Yes |
| health() | Doctor + capabilities TTL | Yes |
| on_bead_written(bead) | After every bead write | Yes |
| on_association_written(assoc) | After every association write | Yes |
| on_bead_retracted(bead_id) | On retraction | Yes |
| traverse(seed_ids, edge_types, max_hops) | graph_traversal=True path | Yes |
| search_candidates(query_text, k, filters) | vector_search=True path | Yes |
| sync_from_storage(beads, associations) | graph backend-sync CLI | Yes |
| close() | Shutdown | Yes |

## BeadSyncTarget protocol (write-only mirrors)

For write-only outputs (e.g. Obsidian vault, external webhook), implement
core_memory.integrations.obsidian.protocol.BeadSyncTarget and register via
CORE_MEMORY_SYNC_TARGETS=obsidian (comma-separated list).

## Injecting an LLM client into GraphitiGraphBackend

register_graph_backend(
    "graphiti",
    lambda: GraphitiGraphBackend.from_env(llm_client=my_graphiti_llm_client),
)

The factory default is llm_client=None (no LLM edge extraction).
See graphiti_core.llm_client for available implementations.

## First-party providers

| Name | Backend | Env vars |
|------|---------|----------|
| kuzu (default) | KuzuGraphBackend | CORE_MEMORY_KUZU_PATH |
| neo4j | Neo4jGraphBackend | CORE_MEMORY_NEO4J_URI/USER/PASSWORD |
| graphiti | GraphitiGraphBackend (local) | CORE_MEMORY_NEO4J_* |
| zep | GraphitiGraphBackend (hosted) | ZEP_API_KEY |
| none / null | NullGraphBackend | — |
```

---

## Step G — `pyproject.toml` updates

### New optional extras (add after `neo4j`):
```toml
graphiti = ["graphiti-core>=0.3", "neo4j>=5.0"]
obsidian = []  # no Python deps; optional REST plugin is user-installed
```

### New pytest markers (add to existing `markers` list in `[tool.pytest.ini_options]`):
```toml
"graphiti: tests that require graphiti-core installed",
"obsidian: tests that exercise the Obsidian vault sync target",
```

---

## Step H — Neo4j live tests + CI job (Phase 7b remainder)

**New file:** `tests/test_neo4j_live.py`
```python
import os
import pytest

pytestmark = [pytest.mark.neo4j, pytest.mark.skipif(
    not os.environ.get("NEO4J_URI"),
    reason="NEO4J_URI not set — live Neo4j test skipped",
)]

# Tests (all use a fresh neo4j driver):
# 1. health() returns {"ok": True}
# 2. on_bead_written + traverse recovers the bead
# 3. on_association_written + traverse returns causal chain
# 4. on_bead_retracted sets status=retracted; traverse excludes it
# 5. sync_from_storage bulk-writes N beads and M associations
```

**File:** `.github/workflows/test.yml` — add new job at the bottom:
```yaml
neo4j-live:
  name: "pytest (neo4j live)"
  runs-on: ubuntu-latest
  if: github.event_name == 'workflow_dispatch'
  services:
    neo4j:
      image: neo4j:5
      env:
        NEO4J_AUTH: neo4j/testpass
      ports: ["7687:7687"]
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    - run: pip install -e ".[dev]" neo4j
    - run: |
        until python -c "from neo4j import GraphDatabase; GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j','testpass')).verify_connectivity()"; do sleep 2; done
    - run: pytest tests/test_neo4j_live.py -v -m neo4j
      env:
        NEO4J_URI: bolt://localhost:7687
        NEO4J_USER: neo4j
        NEO4J_PASSWORD: testpass
```

This job runs only on `workflow_dispatch` — never blocks PR CI.

---

## Step I — Archive `v2_p*` files (Phase 10a)

```bash
cd /home/user/Core-Memory/docs
git mv v2_p9_kickoff.md archive/history/
git mv v2_p17_consolidate_gate.md archive/history/
git mv v2_p17_kickoff.md archive/history/
git mv v2_p18_closeout_checklist.md archive/history/
git mv v2_p18_kickoff.md archive/history/
git mv v2_p19_closeout_checklist.md archive/history/
git mv v2_p19_kickoff.md archive/history/
git mv v2_p20_closeout_checklist.md archive/history/
git mv v2_p20_kickoff.md archive/history/
git mv v2_p21_kickoff.md archive/history/
git mv v2_p22_notes.md archive/history/
```

None of these are linked in `docs/index.md` — confirmed safe to move.

---

## Step J — Retire `docs/ARCHITECTURE.md` (Phase 10b)

1. Read `docs/ARCHITECTURE.md` — extract any content not in `architecture_overview.md`.
   Expected unique content: "Five Canonical Centers" framing from the pre-v2 design.
   If still accurate, add a one-paragraph "Design philosophy" note to `architecture_overview.md`.
   If superseded, discard.

2. `git mv docs/ARCHITECTURE.md docs/archive/history/ARCHITECTURE.md`

3. In `docs/index.md`, find the `ARCHITECTURE.md` link and replace with:
   `architecture_overview.md` (current — see also `archive/history/ARCHITECTURE.md` for pre-v2 Five Centers framing)

---

## Step K — Update `architecture_overview.md` (Phase 10c)

Replace the entire file. Target: 100–150 lines, structured around the 5 mandated questions.

**Section structure:**
```
# Core Memory — Architecture Overview

## 1. Layers and ownership
[table: layer name | owns | imports from]

## 2. Write path
emit_turn_finalized
  → runtime/turn/turn_flow.py: validate, emit memory event, claim pass
  → association/crawler_contract.py: agent-judged causal links
  → runtime/queue/side_effect_queue.py: dreamer, neo4j/graphiti sync, enrichment
  → persistence/store_add_bead_ops.py: local write + graph/sync-target mirrors

## 3. Read path (recall)
recall(query, effort=)
  → retrieval/agent.py: effort tier dispatch
  → retrieval/pipeline/canonical.py:
      tier 1: rolling window (always)
      tier 2: entity registry + semantic index (effort ≥ medium)
      tier 3: causal graph traversal (effort = high or graph_traversal=True)
      tier 4: turn hydration (source-grounded answers)

## 4. Integration pattern
All integrations consume only core_memory/__init__.py.
No adapter imports runtime/*, persistence/*, or schema/* directly.
Adapters: MCP, HTTP/FastAPI, OpenClaw, PydanticAI, LangChain, CrewAI, SpringAI.

## 5. Pluggable components
StorageBackend: JsonFileBackend (default) | SqliteBackend | custom
GraphBackend: Kuzu (default) | Neo4j | Graphiti | Zep | NullGraphBackend | custom
VectorBackend: Qdrant+FastEmbed (default) | FAISS | pgvector | Chroma
BeadSyncTarget: ObsidianSyncTarget | custom

## Directory layout (post-Phase-9)
[abbreviated tree showing subpackage structure]
```

---

## Step L — Classify docs root + update semantic docs (Phase 10d)

### File moves (no content edits):
```bash
cd /home/user/Core-Memory/docs
git mv reranker-paths.md reports/
git mv retrieval-canonical-v9-execution.md reports/
git mv schema_inventory_baseline.md reports/
git mv REFACTOR_NOTES.md archive/
git mv adapter_layer_inventory.md archive/
git mv springai_adapter.md archive/
```

### Content edits:

**`docs/canonical_paths.md`** — update all file paths that changed in Phase 9:
- `event_ingress.py` → `runtime/turn/ingress.py`
- `event_state.py` → `runtime/state.py`
- `event_worker.py` → `runtime/queue/worker.py`
- `memory_engine.py` → `runtime/engine.py`
- `dreamer.py` → `runtime/dreamer/candidates.py` (main logic)
- `cli.py` → `cli/__init__.py`
- flat `openclaw_*.py` files → `integrations/openclaw/`

**`docs/semantic_backend_modes.md`** — add new section after the existing mode table:

```markdown
## Qdrant + FastEmbed (recommended default, zero API key)

When `CORE_MEMORY_VECTOR_BACKEND=qdrant` (or unset, since qdrant is the default):

- **Build path:** `build_semantic_index()` calls `client.add()` which creates a
  FastEmbed-managed collection. `dimension=0` is passed to `_create_external_backend`
  so no `VectorParams` collection is pre-created — incompatible with FastEmbed's own
  collection schema.
- **Manifest stamp:** `provider="fastembed"` is written to `semantic/manifest.json`.
- **Lookup path:** When `manifest["provider"] == "fastembed"`, `semantic_lookup()`
  calls `vb.hybrid_search(query_text)` instead of `_embed_vectors() + vb.search()`.
  No external API key is needed at query time.
- **Startup check:** `has_faiss=True` alone does NOT satisfy `required` mode. FAISS is
  a vector index, not an embedding provider — it still requires an external API key.
  The guard only clears when `has_provider OR has_external OR has_qdrant_fastembed`.

## Deployment profile mapping

| Config | Profile | Notes |
|--------|---------|-------|
| No vector backend | `local_only` | Lexical fallback only |
| `local-faiss` | `local_only` | Single-process write-safe |
| `qdrant` (default) | `distributed_safe` | FastEmbed embedded mode |
| `qdrant` + external embed | `distributed_safe` | Requires API key at build time only |
| `pgvector` | `distributed_safe` | Requires Postgres |
```

---

## Step M — Create `docs/status.md`; align `CLAUDE.md` (Phase 10e)

**New file:** `docs/status.md`

Structure:
```markdown
# Core Memory — Open Work

> This is the single authoritative source for completion state.
> `docs/cleanup-plan.md` and `CLAUDE.md` link here; do not duplicate phase state in those files.

## Engine correctness items
Source: demo/TODO.md + docs/reports/todo-validation-2026-05-15.md
[table of 7 items with current Open/Closed/Partial status]

## Cleanup workstream
[copy phase table from cleanup-plan.md with accurate [x]/[ ] state]

## Deferred items
- Phase 7e–7i: Graphiti, Zep, Obsidian, plugin docs — shipped; see `docs/status.md`
- Phase 7b live tests: workflow_dispatch only; not a PR blocker
- Phase 7 GraphitiLLM injection: user-facing; documented in graph_backend_plugin.md
```

**`CLAUDE.md` phase table** — replace stale phase entries with accurate status
(cross-referenced from `docs/cleanup-plan.md`) and add a note:
```
Authoritative completion state: `docs/status.md`
```

**`demo/TODO.md`** — prepend:
```
> Completion state tracked in [docs/status.md](../status.md). This file retains
> cross-repo references for the Core-Memory-Demo repository.
```

---

## Step N — `docs/PRD/README.md` (Phase 10f)

**New file:** `docs/PRD/README.md`

```markdown
# PRDs

| File | Phase | Title | Actual status |
|------|-------|-------|---------------|
| 00-ci-baseline.md | 0 | CI + Coverage Baseline | Complete |
| 01-dead-file-removal.md | 1 | Delete confirmed dead files | Complete |
| 02-circular-import-fix.md | 2 | Fix mislabelled circular-import workarounds | Complete |
| 03-mcp-protocol-server.md | 3 | MCP Protocol Server | Complete |
| 03a-pydanticai-boundary.md | 3A | Harden PydanticAI boundary | Complete |
| 04-graph-module-cleanup.md | 4 | Classify graph/api.py compat facade | Retained public compatibility |
| 05-persistence-delegation-flatten.md | 5 | Flatten persistence delegation chain | Complete |
| 06-storage-adapter-boundary.md | 6 | Unify StorageBackend capability tiers | Complete |
| 07-neo4j-query-backend.md | 7 | Graph backend abstraction | Complete through 7i; live provider tests env-gated |
| 07b-execution-plan.md | 7b | Neo4j execution plan | Complete |
| 07b-qdrant-kuzu-migration.md | 7b | Qdrant/Kuzu migration | Complete |
| 08-init-wizard.md | 8 | core-memory init wizard + doctor | Complete |
| 09-structural-consolidation.md | 9 | Structural consolidation | Complete (9a–9h) |
| 10-documentation-consolidation.md | 10 | Documentation consolidation | Complete |
| execution-plan-phases-0-7-10.md | 0,7,10 | One-pass execution plan | Complete |
```

---

## Step O — Update `docs/index.md` (Phase 10g)

Six targeted edits:

1. **Architecture section**: remove `ARCHITECTURE.md` link; keep only `architecture_overview.md`
   with note "(archived pre-v2 version in `archive/history/`)"

2. **New "Open workstreams" section** (add near top, after intro):
   ```markdown
   ## Open workstreams
   - [Status and open items](../status.md) — single authoritative source
   - [Cleanup plan](../cleanup-plan.md) — phase sequencing and guard rails
   - [PRDs](README.md) — per-phase implementation specs
   ```

3. **Adapters section**: change "Neo4j (shadow graph)" → "Neo4j / Kuzu / Graphiti / Zep (causal graph backends)"

4. **Remove links** to files moved in Steps I and L:
   - `v2_p*` files (moved to `archive/history/`)
   - `reranker-paths.md`, `retrieval-canonical-v9-execution.md`, `schema_inventory_baseline.md`
     (moved to `reports/`)
   - `REFACTOR_NOTES.md`, `adapter_layer_inventory.md`, `springai_adapter.md`
     (moved to `archive/`)

5. **Add link** to `docs/graph_backend_plugin.md` under an "Integrations" or "Plugins" section.

6. **Verify `eval/` links**: confirm `eval/longitudinal_benchmark.py` exists at that path
   after Phase 9e (`runtime/dreamer/longitudinal.py` is the new home of the logic;
   `eval/` may have a separate benchmark script). Fix any broken links found.

---

## Step P — Phase 0 bookkeeping (no code work)

**File:** `docs/cleanup-plan.md`

Change all `[ ]` checkboxes in Phase 0 to `[x]`. Every item was implemented before
this branch existed — test.yml with three jobs, markers in pyproject.toml, facade/
mixin/pydanticai marks on all target test files. The checkboxes were never updated.

---

## Verification checklist (run after all steps)

```bash
# Markers registered — no PytestUnknownMarkWarning
pytest tests/ --collect-only -q 2>&1 | grep -i "unknown mark"

# New marks visible
pytest tests/ -m graphiti --collect-only -q
pytest tests/ -m obsidian --collect-only -q
pytest tests/ -m neo4j --collect-only -q

# Factory routes correctly
python -c "
import os; os.environ['CORE_MEMORY_GRAPH_BACKEND']='graphiti'
from core_memory.persistence.graph.factory import create_graph_backend
# Expect ImportError or GraphitiGraphBackend, never NullGraphBackend silently
"

# BeadSyncTarget recognized
python -c "
from core_memory.integrations.obsidian import BeadSyncTarget, ObsidianSyncTarget
assert isinstance(ObsidianSyncTarget(vault_path=''), BeadSyncTarget)
"

# Obsidian no-vault no-raise
python -c "
from core_memory.integrations.obsidian import ObsidianSyncTarget
st = ObsidianSyncTarget(vault_path='')
st.on_bead_written({'id':'x','type':'t','title':'T','session_id':'s'})
st.on_association_written({'source_bead':'x','target_bead':'y'})
st.on_bead_retracted('x')
print('ok')
"

# Full test suite still green
pytest tests/ -x -q --tb=short
```

---

## Files created/modified in this pass

| File | Action |
|------|--------|
| `core_memory/persistence/graph/graphiti_backend.py` | New |
| `core_memory/persistence/graph/factory.py` | Edit (add graphiti + zep blocks) |
| `core_memory/runtime/queue/side_effect_queue.py` | Edit (add graphiti-episode-add kind) |
| `core_memory/runtime/queue/side_effects.py` | Edit (enqueue trigger) |
| `core_memory/integrations/obsidian/__init__.py` | New |
| `core_memory/integrations/obsidian/protocol.py` | New |
| `core_memory/integrations/obsidian/vault.py` | New |
| `core_memory/persistence/store.py` | Edit (add _sync_targets) |
| `core_memory/persistence/store_init_ops.py` | Edit (add _create_sync_targets) |
| `core_memory/persistence/store_add_bead_ops.py` | Edit (_mirror_bead_to_backends) |
| `core_memory/persistence/store_relationship_ops.py` | Edit (on_association_written loop) |
| `pyproject.toml` | Edit (graphiti extra + 2 new markers) |
| `.github/workflows/test.yml` | Edit (neo4j-live workflow_dispatch job) |
| `tests/test_graphiti_backend.py` | New |
| `tests/test_neo4j_live.py` | New |
| `tests/test_obsidian_sync_target.py` | New |
| `docs/graph_backend_plugin.md` | New |
| `docs/PRD/README.md` | New |
| `docs/status.md` | New |
| `docs/architecture_overview.md` | Edit (rewrite to 5 questions, post-Phase-9 layout) |
| `docs/canonical_paths.py` | Edit (Phase 9 path updates) |
| `docs/semantic_backend_modes.md` | Edit (FastEmbed section) |
| `docs/index.md` | Edit (6 targeted changes) |
| `docs/ARCHITECTURE.md` | `git mv` → `archive/history/` |
| `docs/v2_p*.md` (11 files) | `git mv` → `archive/history/` |
| `docs/reranker-paths.md` etc. (3 files) | `git mv` → `reports/` |
| `docs/REFACTOR_NOTES.md` etc. (3 files) | `git mv` → `archive/` |
| `docs/cleanup-plan.md` | Edit (check Phase 0 boxes) |
| `CLAUDE.md` | Edit (update phase table; link to status.md) |
| `demo/TODO.md` | Edit (prepend pointer to status.md) |
