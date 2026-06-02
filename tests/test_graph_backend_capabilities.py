"""Phase 7b: Neo4jGraphBackend capabilities() reflects live health probe with TTL."""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch


def _make_backend(healthy: bool = True):
    """Create a Neo4jGraphBackend with a mocked driver."""
    from core_memory.persistence.graph.neo4j_backend import Neo4jGraphBackend

    mock_driver = MagicMock()
    if healthy:
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_driver.session.return_value)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_driver.session.return_value.run.return_value.single.return_value = {"ok": 1}
    else:
        mock_driver.session.side_effect = Exception("connection refused")

    with patch("neo4j.GraphDatabase.driver", return_value=mock_driver):
        backend = Neo4jGraphBackend(uri="bolt://localhost:7687", user="neo4j", password="test")
    backend._driver = mock_driver
    return backend, mock_driver


class TestNeo4jCapabilitiesHealthProbe(unittest.TestCase):
    def test_healthy_backend_returns_graph_traversal_true(self):
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self.skipTest("neo4j package not installed")
        backend, _ = _make_backend(healthy=True)
        caps = backend.capabilities()
        self.assertTrue(caps.graph_traversal)

    def test_unhealthy_backend_returns_all_false(self):
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self.skipTest("neo4j package not installed")
        backend, _ = _make_backend(healthy=False)
        caps = backend.capabilities()
        self.assertFalse(caps.graph_traversal)

    def test_capabilities_cached_within_ttl(self):
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self.skipTest("neo4j package not installed")
        backend, mock_driver = _make_backend(healthy=True)
        backend._health_ttl_s = 60.0

        # First call triggers health probe
        caps1 = backend.capabilities()
        call_count_after_first = mock_driver.session.call_count

        # Second call within TTL must NOT trigger another probe
        caps2 = backend.capabilities()
        self.assertEqual(call_count_after_first, mock_driver.session.call_count)
        self.assertEqual(caps1.graph_traversal, caps2.graph_traversal)

    def test_capabilities_rechecked_after_ttl_expires(self):
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self.skipTest("neo4j package not installed")
        backend, mock_driver = _make_backend(healthy=True)
        backend._health_ttl_s = 0.0  # expire immediately

        backend.capabilities()
        first_count = mock_driver.session.call_count

        # Next call must trigger re-probe since TTL = 0
        backend.capabilities()
        self.assertGreater(mock_driver.session.call_count, first_count)

    def test_health_recovery_after_ttl(self):
        """Backend that was unhealthy reports correct caps after health recovers."""
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self.skipTest("neo4j package not installed")
        backend, mock_driver = _make_backend(healthy=False)
        backend._health_ttl_s = 0.0  # always recheck

        caps_bad = backend.capabilities()
        self.assertFalse(caps_bad.graph_traversal)

        # Restore health
        mock_driver.session.side_effect = None
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_driver.session.return_value)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_driver.session.return_value.run.return_value.single.return_value = {"ok": 1}

        caps_good = backend.capabilities()
        self.assertTrue(caps_good.graph_traversal)


if __name__ == "__main__":
    unittest.main()
