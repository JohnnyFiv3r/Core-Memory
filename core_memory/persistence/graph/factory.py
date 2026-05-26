from __future__ import annotations

import os
from pathlib import Path

from .protocol import GraphBackend, NullGraphBackend


def create_graph_backend(root: Path) -> GraphBackend:
    """Factory for graph backends. Reads CORE_MEMORY_GRAPH_BACKEND env var.

    Defaults to kuzu (embedded, zero-ops). Set CORE_MEMORY_GRAPH_BACKEND=none to
    disable graph traversal and fall back to the Python causal walker.
    """
    name = os.environ.get("CORE_MEMORY_GRAPH_BACKEND", "kuzu").strip().lower()

    if name in ("none", ""):
        return NullGraphBackend()

    if name == "kuzu":
        from .kuzu_backend import KuzuGraphBackend
        path_str = os.environ.get("CORE_MEMORY_KUZU_PATH") or str(Path(root) / ".beads" / "kuzu")
        return KuzuGraphBackend(Path(path_str))

    if name == "neo4j":
        from .neo4j_backend import Neo4jGraphBackend
        return Neo4jGraphBackend.from_env()

    raise ValueError(f"unknown_graph_backend:{name!r} — supported: kuzu, neo4j, none")
