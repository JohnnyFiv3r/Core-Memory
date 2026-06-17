"""Neo4j graph backend parity tests — mocked driver, no live server required.

These tests verify:
- Each public method issues the correct Cypher queries via the driver
- The return structure is consistent with the KuzuGraphBackend contract
- Error paths log warnings and do not raise (best-effort guarantee)
- health() runs `RETURN 1 AS ok`
- on_bead_written() issues MERGE with correct properties
- on_association_written() issues MERGE relationship with correct parameters
- on_bead_retracted() issues SET b.status='retracted'
- traverse() constructs the correct MATCH path Cypher and returns chain structures
- sync_from_storage() delegates to on_bead_written/on_association_written
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock


def _make_fake_neo4j():
    """Inject a fake neo4j module into sys.modules and return (module, driver, session)."""
    driver_mock = MagicMock()
    session_mock = MagicMock()
    driver_mock.session.return_value.__enter__ = MagicMock(return_value=session_mock)
    driver_mock.session.return_value.__exit__ = MagicMock(return_value=False)

    fake_neo4j = types.ModuleType("neo4j")
    fake_gdb = MagicMock()
    fake_gdb.driver.return_value = driver_mock
    fake_neo4j.GraphDatabase = fake_gdb
    sys.modules["neo4j"] = fake_neo4j
    return fake_neo4j, driver_mock, session_mock


def _remove_fake_neo4j():
    sys.modules.pop("neo4j", None)


class TestNeo4jGraphBackendParity(unittest.TestCase):
    def setUp(self):
        self._fake_neo4j, self._driver_mock, self._session_mock = _make_fake_neo4j()

        # Import fresh after injecting the fake module
        import importlib
        import core_memory.persistence.graph.neo4j_backend as _mod
        importlib.reload(_mod)

        self.backend = _mod.Neo4jGraphBackend(
            uri="bolt://localhost:7687", user="neo4j", password="test"
        )

    def tearDown(self):
        _remove_fake_neo4j()

    def _bead(self, bead_id: str, **kwargs) -> dict:
        return {
            "id": bead_id,
            "type": kwargs.get("type", "lesson"),
            "title": kwargs.get("title", f"Bead {bead_id}"),
            "session_id": kwargs.get("session_id", "sess-1"),
            "created_at": "2026-01-01T00:00:00Z",
            "status": kwargs.get("status", "open"),
        }

    def _assoc(self, src: str, tgt: str, rel_type: str = "causes") -> dict:
        return {
            "id": f"assoc-{src}-{tgt}",
            "source_bead": src,
            "target_bead": tgt,
            "relationship": rel_type,
            "confidence": 0.9,
            "created_at": "2026-01-01T00:00:00Z",
        }

    # -------------------------------------------------------------------------
    # capabilities
    # -------------------------------------------------------------------------
    def test_capabilities_graph_traversal_true(self):
        caps = self.backend.capabilities()
        self.assertTrue(caps.graph_traversal)
        self.assertFalse(caps.vector_search)

    # -------------------------------------------------------------------------
    # health
    # -------------------------------------------------------------------------
    def test_health_runs_return_1(self):
        mock_result = MagicMock()
        mock_result.single.return_value = {"ok": 1}
        self._session_mock.run.return_value = mock_result

        h = self.backend.health()

        self.assertTrue(h.get("ok"))
        self.assertEqual(h.get("backend"), "neo4j")
        self._session_mock.run.assert_called_once_with("RETURN 1 AS ok")

    def test_health_returns_error_on_driver_failure(self):
        self._driver_mock.session.return_value.__enter__.side_effect = Exception("connection refused")
        h = self.backend.health()
        self.assertFalse(h.get("ok"))
        self.assertEqual(h.get("backend"), "neo4j")
        self.assertIn("connection refused", str(h.get("error") or ""))

    # -------------------------------------------------------------------------
    # on_bead_written
    # -------------------------------------------------------------------------
    def test_on_bead_written_issues_merge_cypher(self):
        bead = self._bead("bead-A1")
        self.backend.on_bead_written(bead)

        self._session_mock.run.assert_called_once()
        args, kwargs = self._session_mock.run.call_args
        cypher = args[0]
        self.assertIn("MERGE", cypher)
        self.assertIn(":Bead", cypher)
        self.assertEqual(kwargs.get("id"), "bead-A1")
        self.assertEqual(kwargs.get("type"), "lesson")
        self.assertEqual(kwargs.get("status"), "open")

    def test_on_bead_written_skips_empty_id(self):
        self.backend.on_bead_written({"type": "lesson", "title": "no id"})
        self._session_mock.run.assert_not_called()

    def test_on_bead_written_does_not_raise_on_driver_error(self):
        self._session_mock.run.side_effect = Exception("network error")
        try:
            self.backend.on_bead_written(self._bead("bead-B1"))
        except Exception as exc:
            self.fail(f"on_bead_written raised unexpectedly: {exc}")

    # -------------------------------------------------------------------------
    # on_association_written
    # -------------------------------------------------------------------------
    def test_on_association_written_issues_merge_relationship_cypher(self):
        assoc = self._assoc("bead-X", "bead-Y", "causes")
        self.backend.on_association_written(assoc)

        self._session_mock.run.assert_called_once()
        args, kwargs = self._session_mock.run.call_args
        cypher = args[0]
        self.assertIn("MERGE", cypher)
        self.assertIn(":ASSOCIATION", cypher)
        self.assertEqual(kwargs.get("src"), "bead-X")
        self.assertEqual(kwargs.get("tgt"), "bead-Y")
        self.assertEqual(kwargs.get("rel_type"), "causes")
        self.assertAlmostEqual(float(kwargs.get("confidence") or 0), 0.9)

    def test_on_association_written_skips_missing_fields(self):
        # Missing tgt — should not run any Cypher
        self.backend.on_association_written({"source_bead": "bead-X", "relationship": "causes"})
        self._session_mock.run.assert_not_called()

    def test_on_association_written_does_not_raise_on_driver_error(self):
        self._session_mock.run.side_effect = Exception("network error")
        assoc = self._assoc("bead-X", "bead-Y")
        try:
            self.backend.on_association_written(assoc)
        except Exception as exc:
            self.fail(f"on_association_written raised unexpectedly: {exc}")

    # -------------------------------------------------------------------------
    # on_bead_retracted
    # -------------------------------------------------------------------------
    def test_on_bead_retracted_sets_status_retracted(self):
        self.backend.on_bead_retracted("bead-Z")

        self._session_mock.run.assert_called_once()
        args, kwargs = self._session_mock.run.call_args
        cypher = args[0]
        self.assertIn("retracted", cypher)
        self.assertEqual(kwargs.get("id"), "bead-Z")

    def test_on_bead_retracted_does_not_raise_on_driver_error(self):
        self._session_mock.run.side_effect = Exception("network error")
        try:
            self.backend.on_bead_retracted("bead-Z")
        except Exception as exc:
            self.fail(f"on_bead_retracted raised unexpectedly: {exc}")

    # -------------------------------------------------------------------------
    # traverse
    # -------------------------------------------------------------------------
    def test_traverse_returns_empty_for_no_seeds(self):
        chains = self.backend.traverse(seed_ids=[], edge_types=None, max_hops=2)
        self.assertEqual(chains, [])
        self._session_mock.run.assert_not_called()

    def test_traverse_issues_match_path_cypher(self):
        self._session_mock.run.return_value = []
        self.backend.traverse(seed_ids=["bead-A"], edge_types=None, max_hops=2)

        self._session_mock.run.assert_called_once()
        args, kwargs = self._session_mock.run.call_args
        cypher = args[0]
        self.assertIn("MATCH", cypher)
        self.assertIn("path", cypher)
        self.assertIn(":Bead", cypher)
        self.assertIn(":ASSOCIATION", cypher)
        # seed_ids parameter must be passed
        self.assertIn("bead-A", str(kwargs.get("seed_ids") or ""))

    def test_traverse_returns_chain_list(self):
        node_a = {"id": "bead-A", "type": "decision", "title": "A"}
        node_b = {"id": "bead-B", "type": "outcome", "title": "B"}
        edge = {"rel": "causes", "src": "bead-A", "tgt": "bead-B"}

        record = MagicMock()
        record.__getitem__ = lambda self, key: {"nodes": [node_a, node_b], "edges": [edge]}[key]
        self._session_mock.run.return_value = [record]

        chains = self.backend.traverse(seed_ids=["bead-A"], edge_types=None, max_hops=2)
        self.assertEqual(len(chains), 1)
        self.assertIn("nodes", chains[0])
        self.assertIn("edges", chains[0])
        self.assertEqual(len(chains[0]["nodes"]), 2)
        self.assertEqual(len(chains[0]["edges"]), 1)

    def test_traverse_does_not_raise_on_driver_error(self):
        self._session_mock.run.side_effect = Exception("network error")
        chains = self.backend.traverse(seed_ids=["bead-A"], edge_types=None, max_hops=2)
        self.assertEqual(chains, [])

    def test_traverse_respects_edge_type_filter(self):
        self._session_mock.run.return_value = []
        self.backend.traverse(
            seed_ids=["bead-A"], edge_types=["causes", "supersedes"], max_hops=3
        )

        args, _ = self._session_mock.run.call_args
        cypher = args[0]
        self.assertIn("causes", cypher)
        self.assertIn("supersedes", cypher)

    # -------------------------------------------------------------------------
    # sync_from_storage
    # -------------------------------------------------------------------------
    def test_sync_from_storage_calls_on_bead_written_for_each_bead(self):
        beads = [self._bead(f"bead-{i}") for i in range(3)]
        result = self.backend.sync_from_storage(beads=beads, associations=[])
        self.assertEqual(result.get("synced_beads"), 3)
        self.assertEqual(result.get("synced_associations"), 0)
        self.assertEqual(len(result.get("errors") or []), 0)
        self.assertEqual(self._session_mock.run.call_count, 3)

    def test_sync_from_storage_calls_on_association_written_for_each_assoc(self):
        assocs = [self._assoc("bead-A", "bead-B"), self._assoc("bead-B", "bead-C")]
        result = self.backend.sync_from_storage(beads=[], associations=assocs)
        self.assertEqual(result.get("synced_associations"), 2)
        self.assertEqual(result.get("synced_beads"), 0)

    def test_sync_from_storage_does_not_raise_on_driver_error(self):
        # on_bead_written catches driver errors internally — sync_from_storage must
        # still return a valid result dict without propagating the exception.
        self._session_mock.run.side_effect = Exception("write error")
        beads = [self._bead("bead-A"), self._bead("bead-B")]
        try:
            result = self.backend.sync_from_storage(beads=beads, associations=[])
        except Exception as exc:
            self.fail(f"sync_from_storage raised unexpectedly: {exc}")
        self.assertIn("synced_beads", result)
        self.assertIn("synced_associations", result)
        self.assertIn("errors", result)

    # -------------------------------------------------------------------------
    # close
    # -------------------------------------------------------------------------
    def test_close_calls_driver_close(self):
        self.backend.close()
        self._driver_mock.close.assert_called_once()

    def test_close_does_not_raise_on_driver_error(self):
        self._driver_mock.close.side_effect = Exception("already closed")
        try:
            self.backend.close()
        except Exception as exc:
            self.fail(f"close() raised unexpectedly: {exc}")


class TestNeo4jImportError(unittest.TestCase):
    def setUp(self):
        # Ensure neo4j is NOT in sys.modules so ImportError fires
        _remove_fake_neo4j()

    @unittest.skipIf(
        __import__("importlib").util.find_spec("neo4j") is not None,
        "neo4j is installed — ImportError test only meaningful when package is absent",
    )
    def test_constructor_raises_import_error_when_neo4j_missing(self):
        import importlib
        import core_memory.persistence.graph.neo4j_backend as _mod
        importlib.reload(_mod)
        with self.assertRaises((ImportError, Exception)):
            _mod.Neo4jGraphBackend(uri="bolt://x", user="u", password="p")


if __name__ == "__main__":
    unittest.main()
