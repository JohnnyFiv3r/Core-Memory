from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.backend import (
    BackendCapabilities,
    JsonFileBackend,
    SqliteBackend,
    get_backend_capabilities,
)


class TestBackendCapabilitiesDefaults(unittest.TestCase):
    def test_dataclass_all_false_by_default(self):
        caps = BackendCapabilities()
        self.assertFalse(caps.vector_search)
        self.assertFalse(caps.graph_traversal)
        self.assertFalse(caps.full_text_search)
        self.assertFalse(caps.transcript_hydration)

    def test_json_backend_capabilities_all_false(self):
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

    def test_get_backend_capabilities_json(self):
        with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
            caps = get_backend_capabilities(Path(td))
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertFalse(caps.vector_search)
        self.assertFalse(caps.graph_traversal)

    def test_get_backend_capabilities_sqlite_env(self):
        import os
        with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
            old = os.environ.get("CORE_MEMORY_BACKEND")
            try:
                os.environ["CORE_MEMORY_BACKEND"] = "sqlite"
                caps = get_backend_capabilities(Path(td))
            finally:
                if old is None:
                    os.environ.pop("CORE_MEMORY_BACKEND", None)
                else:
                    os.environ["CORE_MEMORY_BACKEND"] = old
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertFalse(caps.vector_search)
        self.assertFalse(caps.graph_traversal)

    def test_get_backend_capabilities_unknown_backend_fallback(self):
        import os
        with tempfile.TemporaryDirectory(prefix="cm-caps-") as td:
            old = os.environ.get("CORE_MEMORY_BACKEND")
            try:
                os.environ["CORE_MEMORY_BACKEND"] = "neo4j"
                caps = get_backend_capabilities(Path(td))
            finally:
                if old is None:
                    os.environ.pop("CORE_MEMORY_BACKEND", None)
                else:
                    os.environ["CORE_MEMORY_BACKEND"] = old
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
