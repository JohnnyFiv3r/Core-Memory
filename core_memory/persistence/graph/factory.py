from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from .protocol import GraphBackend, NullGraphBackend

_log = logging.getLogger(__name__)

# Registry maps provider name → zero-arg factory callable.
# First-party providers self-register at import time via _register_first_party().
_PROVIDERS: dict[str, Callable[[], GraphBackend]] = {
    "none": NullGraphBackend,
    "null": NullGraphBackend,
}


def register_graph_backend(name: str, factory: Callable[[], GraphBackend]) -> None:
    """Plugin hook for custom graph providers. Idempotent re-registration is allowed.

    Call this at module import time with a zero-arg callable that returns a
    configured GraphBackend. The name must match CORE_MEMORY_GRAPH_BACKEND.

    Example::

        from core_memory.persistence.graph import register_graph_backend
        register_graph_backend("mygraph", lambda: MyGraphBackend.from_env())
    """
    _PROVIDERS[name.strip().lower()] = factory


def create_graph_backend(root: Path | None = None) -> GraphBackend:
    """Factory for graph backends. Reads CORE_MEMORY_GRAPH_BACKEND env var.

    Defaults to 'kuzu' (embedded graph, zero server ops).
    Set CORE_MEMORY_GRAPH_BACKEND=none|neo4j|<custom> to change provider.

    On unknown provider name or construction failure, logs a warning and
    returns NullGraphBackend so the caller always gets a valid backend.
    """
    name = (os.environ.get("CORE_MEMORY_GRAPH_BACKEND") or "kuzu").strip().lower()

    factory = _PROVIDERS.get(name)
    if factory is not None:
        try:
            return factory()
        except Exception as exc:
            _log.warning("graph backend %r construction failed (%s); falling back to null", name, exc)
            return NullGraphBackend()

    # Built-in providers not in the registry yet (kuzu, neo4j) — construct directly.
    if name == "kuzu":
        try:
            from .kuzu_backend import KuzuGraphBackend
            path_str = os.environ.get("CORE_MEMORY_KUZU_PATH") or (
                str(Path(root) / ".beads" / "kuzu") if root else ".beads/kuzu"
            )
            return KuzuGraphBackend(Path(path_str))
        except Exception as exc:
            _log.warning("kuzu graph backend construction failed (%s); falling back to null", exc)
            return NullGraphBackend()

    if name == "neo4j":
        try:
            from .neo4j_backend import Neo4jGraphBackend
            return Neo4jGraphBackend.from_env()
        except Exception as exc:
            _log.warning("neo4j graph backend construction failed (%s); falling back to null", exc)
            return NullGraphBackend()

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

    _log.warning("unknown graph backend %r; falling back to null", name)
    return NullGraphBackend()
