from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.backend import (
    BackendCapabilities,
    JsonFileBackend,
    SqliteBackend,
    get_backend_capabilities,
)


def _with_env(overrides: dict) -> "contextlib.AbstractContextManager":
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        old = {k: os.environ.get(k) for k in overrides}
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            yield
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    return _ctx()


class TestBackendCapabilitiesDefaults(unittest.TestCase):
    def test_dataclass_all_false_by_default(self):
        caps = BackendCapabilities()
        self.assertFalse(caps.vector_search)
        self.assertFalse(caps.graph_traversal)
        self.assertFalse(caps.full_text_search)
        self.assertFalse(caps.transcript_hydration)

    def test_json_backend_capabilities_all_false(self):
        # JsonFileBackend.capabilities() always returns all-False (storage-layer, not retrieval-layer)
        with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
            backend = JsonFileBackend(Path(td))
            caps = backend.capabilities()
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertFalse(caps.vector_search)
        self.assertFalse(caps.graph_traversal)
        self.assertFalse(caps.full_text_search)
        self.assertFalse(caps.transcript_hydration)

    def test_sqlite_backend_capabilities_all_false(self):
        with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
            backend = SqliteBackend(Path(td))
            caps = backend.capabilities()
            backend.close()
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertFalse(caps.vector_search)
        self.assertFalse(caps.graph_traversal)

    # get_backend_capabilities reads CORE_MEMORY_VECTOR_BACKEND + CORE_MEMORY_GRAPH_BACKEND.
    # Default is qdrant + kuzu → vector_search=True, graph_traversal=True.

    def test_get_backend_capabilities_qdrant_kuzu_defaults(self):
        with _with_env({"CORE_MEMORY_VECTOR_BACKEND": "qdrant", "CORE_MEMORY_GRAPH_BACKEND": "kuzu"}):
            with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
                caps = get_backend_capabilities(Path(td))
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertTrue(caps.vector_search)
        self.assertTrue(caps.graph_traversal)
        self.assertTrue(caps.full_text_search)

    def test_get_backend_capabilities_no_backends(self):
        with _with_env({"CORE_MEMORY_VECTOR_BACKEND": "local-faiss", "CORE_MEMORY_GRAPH_BACKEND": "none"}):
            with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
                caps = get_backend_capabilities(Path(td))
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertFalse(caps.vector_search)
        self.assertFalse(caps.graph_traversal)

    def test_get_backend_capabilities_neo4j_graph(self):
        with _with_env({"CORE_MEMORY_GRAPH_BACKEND": "neo4j", "CORE_MEMORY_VECTOR_BACKEND": "local-faiss"}):
            with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
                caps = get_backend_capabilities(Path(td))
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertTrue(caps.graph_traversal)
        self.assertFalse(caps.vector_search)

    def test_get_backend_capabilities_sqlite_env(self):
        # CORE_MEMORY_BACKEND (storage) doesn't affect vector/graph caps
        with _with_env({"CORE_MEMORY_BACKEND": "sqlite", "CORE_MEMORY_VECTOR_BACKEND": "qdrant", "CORE_MEMORY_GRAPH_BACKEND": "kuzu"}):
            with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
                caps = get_backend_capabilities(Path(td))
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertTrue(caps.vector_search)
        self.assertTrue(caps.graph_traversal)

    def test_get_backend_capabilities_unknown_vector_backend_fallback(self):
        with _with_env({"CORE_MEMORY_VECTOR_BACKEND": "pgvector", "CORE_MEMORY_GRAPH_BACKEND": "none"}):
            with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
                caps = get_backend_capabilities(Path(td))
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertFalse(caps.vector_search)

    def test_search_candidates_raises_not_implemented(self):
        with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
            backend = JsonFileBackend(Path(td))
        with self.assertRaises(NotImplementedError):
            backend.search_candidates([], None, 10)

    def test_traverse_raises_not_implemented(self):
        with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
            backend = JsonFileBackend(Path(td))
        with self.assertRaises(NotImplementedError):
            backend.traverse([], None, 3)

    def test_hydrate_turn_refs_raises_not_implemented(self):
        with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
            backend = JsonFileBackend(Path(td))
        with self.assertRaises(NotImplementedError):
            backend.hydrate_turn_refs([])


if __name__ == "__main__":
    unittest.main()
