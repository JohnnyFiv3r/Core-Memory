from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from core_memory.persistence.backend import BackendCapabilities

_log = logging.getLogger(__name__)
_DEFAULT_HEALTH_TTL_S = 60.0


@runtime_checkable
class GraphitiLLMClientProtocol(Protocol):
    """Minimal interface Graphiti expects from an LLM client.

    Pass a concrete graphiti_core.llm_client.* instance to enable LLM-augmented
    edge extraction. Passing None (the default) disables LLM extraction —
    episodes are still written as nodes without semantic edge inference.
    The persistence layer never constructs this object; injection only.
    """

    async def generate_response(self, messages: list[dict[str, Any]], **kwargs: Any) -> str: ...


class GraphitiGraphBackend:
    """Graphiti temporal knowledge graph backend.

    Write hooks (on_bead_written, on_association_written) enqueue a
    graphiti-episode-add side_effect_queue job rather than calling asyncio.run()
    inline, keeping turn finalization non-blocking and safe inside async
    contexts (FastAPI, MCP server).

    Read methods (traverse, search_candidates) run via a persistent background
    event loop managed by _get_loop() / _run().

    LLM client injection: the factory always passes llm_client=None.
    Users wanting LLM-augmented edge extraction call register_graph_backend
    with a concrete GraphitiLLMClientProtocol. See docs/graph_backend_plugin.md.
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
        root: str | Path | None = None,
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
            try:
                from graphiti_core.driver.falkordb import FalkorDBDriver
                driver = FalkorDBDriver(url=uri, api_key=zep_api_key)
            except ImportError:
                raise ImportError(
                    "Zep hosted deployment requires falkordb driver in graphiti-core"
                )
        else:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(uri, auth=(user, password))

        self._client = Graphiti(driver=driver, llm_client=llm_client)

        try:
            asyncio.run(self._client.build_indices_and_constraints())
        except Exception as exc:
            _log.warning("graphiti index setup warning (non-fatal): %s", exc)

        self._healthy: bool | None = None
        self._health_checked_at: float = 0.0
        self._health_ttl_s = float(
            os.environ.get("CORE_MEMORY_GRAPH_HEALTH_TTL_S") or _DEFAULT_HEALTH_TTL_S
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        # Store root for enqueuing jobs into the correct per-store side-effect queue.
        # Falls back to DEFAULT_ROOT only when called outside a store context (rare).
        from core_memory.persistence.store import DEFAULT_ROOT
        self._root: Path = Path(root) if root is not None else Path(DEFAULT_ROOT)

    @classmethod
    def from_env(
        cls,
        *,
        deployment: str = "local",
        llm_client: GraphitiLLMClientProtocol | None = None,
        root: str | Path | None = None,
    ) -> "GraphitiGraphBackend":
        return cls(
            uri=os.environ.get("CORE_MEMORY_NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("CORE_MEMORY_NEO4J_USER", "neo4j"),
            password=os.environ.get("CORE_MEMORY_NEO4J_PASSWORD", ""),
            deployment=deployment,
            zep_api_key=os.environ.get("ZEP_API_KEY"),
            llm_client=llm_client,
            root=root,
        )

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever, daemon=True, name="graphiti-bg-loop"
            )
            self._loop_thread.start()
        return self._loop

    def _run(self, coro: Any, timeout: float = 30.0) -> Any:
        fut = asyncio.run_coroutine_threadsafe(coro, self._get_loop())
        return fut.result(timeout=timeout)

    def _needs_recheck(self) -> bool:
        return self._healthy is None or (
            time.monotonic() - self._health_checked_at
        ) >= self._health_ttl_s

    def capabilities(self) -> BackendCapabilities:
        if self._needs_recheck():
            result = self.health()
            self._healthy = bool(result.get("ok"))
            self._health_checked_at = time.monotonic()
        if not self._healthy:
            return BackendCapabilities()
        return BackendCapabilities(graph_traversal=True, vector_search=True)

    def health(self) -> dict:
        try:
            self._run(self._client.driver.verify_connectivity(), timeout=10.0)
            return {"ok": True, "backend": "graphiti"}
        except Exception as exc:
            return {"ok": False, "backend": "graphiti", "error": str(exc)}

    def on_bead_written(self, bead: dict) -> None:
        bead_id = str(bead.get("id") or "")
        if not bead_id:
            return
        try:
            from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event
            enqueue_side_effect_event(
                root=self._root,
                kind="graphiti-episode-add",
                payload={"bead": bead},
                idempotency_key=f"graphiti:bead:{bead_id}",
            )
        except Exception as exc:
            _log.warning("graphiti on_bead_written enqueue failed: %s", exc)

    def on_association_written(self, assoc: dict) -> None:
        assoc_id = str(assoc.get("id") or "")
        if not assoc_id:
            return
        try:
            from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event
            enqueue_side_effect_event(
                root=self._root,
                kind="graphiti-episode-add",
                payload={"assoc": assoc},
                idempotency_key=f"graphiti:assoc:{assoc_id}",
            )
        except Exception as exc:
            _log.warning("graphiti on_association_written enqueue failed: %s", exc)

    def _write_bead_sync(self, bead: dict) -> None:
        """Called by the side_effect_queue worker. Runs the actual Graphiti call."""
        try:
            from graphiti_core.nodes import EpisodeType
        except ImportError:
            EpisodeType = None  # type: ignore[assignment]

        summary = bead.get("summary") or []
        body = "\n".join(summary) if summary else str(bead.get("title") or "")

        kwargs: dict[str, Any] = dict(
            name=str(bead.get("id") or ""),
            episode_body=body,
            reference_time=str(bead.get("created_at") or ""),
            source_description=str(bead.get("session_id") or ""),
            group_id=str(bead.get("type") or ""),
        )
        if EpisodeType is not None:
            kwargs["source"] = EpisodeType.text

        self._run(self._client.add_episode(**kwargs))

    def _write_association_sync(self, assoc: dict) -> None:
        """Graphiti infers relationships from episode content; this is a hint only."""
        _log.debug(
            "graphiti association %s→%s noted (relationship inference via episode content)",
            assoc.get("source_bead"),
            assoc.get("target_bead"),
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
            results = self._run(
                self._client.search(
                    query=" ".join(seed_ids),
                    num_results=max_chains,
                )
            )
            return [
                {
                    "nodes": [{"id": str(r.uuid), "type": "graphiti_fact", "title": str(r.fact)}],
                    "edges": [],
                }
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
                {
                    "bead_id": str(r.uuid),
                    "score": float(getattr(r, "score", 1.0)),
                    "metadata": {"fact": str(r.fact)},
                }
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
        return {
            "synced_beads": bead_count,
            "synced_associations": 0,
            "errors": errors,
        }

    def close(self) -> None:
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
