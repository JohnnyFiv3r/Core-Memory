"""Phase 7a: create_graph_backend factory env-var routing and fallback behaviour."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core_memory.persistence.graph import NullGraphBackend, create_graph_backend, register_graph_backend
from core_memory.persistence.graph.factory import _PROVIDERS

try:
    import kuzu  # noqa: F401
    _KUZU_AVAILABLE = True
except ImportError:
    _KUZU_AVAILABLE = False


class TestCreateGraphBackendEnvRouting(unittest.TestCase):
    @unittest.skipUnless(_KUZU_AVAILABLE, "kuzu not installed")
    def test_no_env_var_defaults_to_kuzu(self):
        # Default is kuzu (embedded, zero-ops). Empty string behaves the same.
        from core_memory.persistence.graph.kuzu_backend import KuzuGraphBackend
        with patch.dict("os.environ", {"CORE_MEMORY_GRAPH_BACKEND": ""}, clear=False):
            gb = create_graph_backend()
        self.assertIsInstance(gb, KuzuGraphBackend)

    def test_none_returns_null(self):
        with patch.dict("os.environ", {"CORE_MEMORY_GRAPH_BACKEND": "none"}, clear=False):
            gb = create_graph_backend()
        self.assertIsInstance(gb, NullGraphBackend)

    def test_null_alias_returns_null(self):
        with patch.dict("os.environ", {"CORE_MEMORY_GRAPH_BACKEND": "null"}, clear=False):
            gb = create_graph_backend()
        self.assertIsInstance(gb, NullGraphBackend)

    def test_unknown_provider_returns_null_not_raise(self):
        with patch.dict("os.environ", {"CORE_MEMORY_GRAPH_BACKEND": "totally_unknown_xyz"}, clear=False):
            gb = create_graph_backend()
        self.assertIsInstance(gb, NullGraphBackend)

    def test_neo4j_missing_dep_returns_null(self):
        import sys
        with patch.dict("os.environ", {"CORE_MEMORY_GRAPH_BACKEND": "neo4j"}, clear=False):
            with patch.dict(sys.modules, {"neo4j": None}):
                gb = create_graph_backend()
        self.assertIsInstance(gb, NullGraphBackend)

    def test_construction_exception_returns_null(self):
        def _bad_factory():
            raise RuntimeError("backend init failed")

        with patch.dict("os.environ", {"CORE_MEMORY_GRAPH_BACKEND": "bad_backend"}, clear=False):
            with patch.dict(_PROVIDERS, {"bad_backend": _bad_factory}):
                gb = create_graph_backend()
        self.assertIsInstance(gb, NullGraphBackend)


class TestRegisterGraphBackend(unittest.TestCase):
    def test_custom_provider_is_callable(self):
        sentinel = NullGraphBackend()
        register_graph_backend("test_custom_abc", lambda: sentinel)

        with patch.dict("os.environ", {"CORE_MEMORY_GRAPH_BACKEND": "test_custom_abc"}, clear=False):
            gb = create_graph_backend()
        self.assertIs(sentinel, gb)

        # cleanup
        _PROVIDERS.pop("test_custom_abc", None)

    def test_reregistration_is_idempotent(self):
        backend_a = NullGraphBackend()
        backend_b = NullGraphBackend()
        register_graph_backend("test_rereg_xyz", lambda: backend_a)
        register_graph_backend("test_rereg_xyz", lambda: backend_b)

        with patch.dict("os.environ", {"CORE_MEMORY_GRAPH_BACKEND": "test_rereg_xyz"}, clear=False):
            gb = create_graph_backend()
        self.assertIs(backend_b, gb)

        _PROVIDERS.pop("test_rereg_xyz", None)


if __name__ == "__main__":
    unittest.main()
