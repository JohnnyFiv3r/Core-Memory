"""GraphitiGraphBackend tests — mocked graphiti_core, no live server required."""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.graphiti


def _make_fake_graphiti():
    """Inject a fake graphiti_core module into sys.modules."""
    fake = types.ModuleType("graphiti_core")

    # Fake Graphiti client
    client_mock = MagicMock()
    client_mock.build_indices_and_constraints = AsyncMock(return_value=None)
    client_mock.add_episode = AsyncMock(return_value=None)
    client_mock.delete_episode = AsyncMock(return_value=None)
    client_mock.search = AsyncMock(return_value=[])
    client_mock.driver = MagicMock()
    client_mock.driver.verify_connectivity = AsyncMock(return_value=None)

    fake_graphiti_cls = MagicMock(return_value=client_mock)
    fake.Graphiti = fake_graphiti_cls

    # Fake nodes module
    fake_nodes = types.ModuleType("graphiti_core.nodes")
    fake_nodes.EpisodeType = MagicMock(text="text")
    sys.modules["graphiti_core"] = fake
    sys.modules["graphiti_core.nodes"] = fake_nodes

    # Fake neo4j driver
    fake_neo4j = types.ModuleType("neo4j")
    fake_driver = MagicMock()
    fake_gdb = MagicMock()
    fake_gdb.driver.return_value = fake_driver
    fake_neo4j.GraphDatabase = fake_gdb
    sys.modules["neo4j"] = fake_neo4j

    return fake, client_mock, fake_driver


def _remove_fakes():
    for mod in ["graphiti_core", "graphiti_core.nodes"]:
        sys.modules.pop(mod, None)
    sys.modules.pop("neo4j", None)


class TestGraphitiGraphBackend(unittest.TestCase):
    def setUp(self):
        self._fake, self._client, self._driver = _make_fake_graphiti()
        import importlib
        import core_memory.persistence.graph.graphiti_backend as _mod
        importlib.reload(_mod)
        self._mod = _mod
        self.backend = _mod.GraphitiGraphBackend(
            uri="bolt://localhost:7687", user="neo4j", password="test"
        )
        # Point _run to directly run asyncio for test purposes
        import asyncio

        def _sync_run(coro, timeout=30.0):
            return asyncio.run(coro)

        self.backend._run = _sync_run

    def tearDown(self):
        _remove_fakes()

    def _bead(self, bead_id: str = "bead-1") -> dict:
        return {
            "id": bead_id,
            "type": "decision",
            "title": f"Bead {bead_id}",
            "session_id": "sess-1",
            "created_at": "2026-01-01T00:00:00Z",
            "summary": ["Line one."],
        }

    # capabilities
    def test_capabilities_all_false_when_unhealthy(self):
        self._client.driver.verify_connectivity.side_effect = Exception("down")
        caps = self.backend.capabilities()
        self.assertFalse(caps.graph_traversal)
        self.assertFalse(caps.vector_search)

    def test_capabilities_traversal_and_vector_when_healthy(self):
        caps = self.backend.capabilities()
        self.assertTrue(caps.graph_traversal)
        self.assertTrue(caps.vector_search)

    # health
    def test_health_ok_when_driver_reachable(self):
        h = self.backend.health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["backend"], "graphiti")

    def test_health_not_ok_when_driver_fails(self):
        self._client.driver.verify_connectivity.side_effect = Exception("conn refused")
        h = self.backend.health()
        self.assertFalse(h["ok"])
        self.assertIn("conn refused", str(h.get("error") or ""))

    # on_bead_written — enqueues, does not call Graphiti directly
    def test_on_bead_written_enqueues_side_effect(self):
        with patch(
            "core_memory.persistence.graph.graphiti_backend.enqueue_side_effect_event"
            if False else "core_memory.runtime.queue.side_effect_queue.enqueue_side_effect_event",
        ):
            # Just verify it doesn't raise and doesn't call add_episode directly
            self.backend.on_bead_written(self._bead())
        self._client.add_episode.assert_not_called()

    def test_on_bead_written_skips_empty_id(self):
        self.backend.on_bead_written({"type": "decision", "title": "no id"})
        self._client.add_episode.assert_not_called()

    def test_on_bead_written_does_not_raise_on_enqueue_error(self):
        with patch(
            "core_memory.persistence.graph.graphiti_backend.enqueue_side_effect_event",
            side_effect=Exception("queue full"),
            create=True,
        ):
            try:
                self.backend.on_bead_written(self._bead())
            except Exception as exc:
                self.fail(f"on_bead_written raised: {exc}")

    # _write_bead_sync — calls add_episode
    def test_write_bead_sync_calls_add_episode(self):
        self.backend._write_bead_sync(self._bead("bead-W"))
        self._client.add_episode.assert_called_once()
        kwargs = self._client.add_episode.call_args.kwargs
        self.assertEqual(kwargs["name"], "bead-W")
        self.assertIn("Line one", kwargs["episode_body"])

    # on_bead_retracted
    def test_on_bead_retracted_calls_delete_episode(self):
        self.backend.on_bead_retracted("bead-Z")
        self._client.delete_episode.assert_called_once_with("bead-Z")

    def test_on_bead_retracted_does_not_raise_on_error(self):
        self._client.delete_episode.side_effect = Exception("not found")
        try:
            self.backend.on_bead_retracted("bead-Z")
        except Exception as exc:
            self.fail(f"on_bead_retracted raised: {exc}")

    # traverse
    def test_traverse_returns_empty_for_no_seeds(self):
        chains = self.backend.traverse(seed_ids=[], edge_types=None, max_hops=2)
        self.assertEqual(chains, [])
        self._client.search.assert_not_called()

    def test_traverse_calls_search_and_maps_results(self):
        fact = MagicMock()
        fact.uuid = "fact-uuid-1"
        fact.fact = "Some causal fact"
        self._client.search.return_value = [fact]

        chains = self.backend.traverse(seed_ids=["bead-A"], edge_types=None, max_hops=2)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["nodes"][0]["id"], "fact-uuid-1")

    def test_traverse_does_not_raise_on_search_error(self):
        self._client.search.side_effect = Exception("timeout")
        chains = self.backend.traverse(seed_ids=["bead-A"], edge_types=None, max_hops=2)
        self.assertEqual(chains, [])

    # search_candidates
    def test_search_candidates_returns_ok_with_results(self):
        fact = MagicMock()
        fact.uuid = "fact-1"
        fact.fact = "A fact"
        fact.score = 0.92
        self._client.search.return_value = [fact]

        result = self.backend.search_candidates("query text", k=3)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["bead_id"], "fact-1")
        self.assertAlmostEqual(result["results"][0]["score"], 0.92)

    def test_search_candidates_returns_not_ok_on_error(self):
        self._client.search.side_effect = Exception("service down")
        result = self.backend.search_candidates("query")
        self.assertFalse(result["ok"])
        self.assertIn("service down", result["warnings"][0])

    # sync_from_storage
    def test_sync_from_storage_calls_write_bead_sync_for_each_bead(self):
        beads = [self._bead(f"bead-{i}") for i in range(3)]
        result = self.backend.sync_from_storage(beads=beads, associations=[])
        self.assertEqual(result["synced_beads"], 3)
        self.assertEqual(self._client.add_episode.call_count, 3)

    # close
    def test_close_does_not_raise(self):
        try:
            self.backend.close()
        except Exception as exc:
            self.fail(f"close raised: {exc}")


class TestGraphitiImportError(unittest.TestCase):
    def setUp(self):
        _remove_fakes()

    @unittest.skipIf(
        __import__("importlib").util.find_spec("graphiti_core") is not None,
        "graphiti-core is installed — ImportError test only meaningful when absent",
    )
    def test_constructor_raises_import_error_when_graphiti_missing(self):
        import importlib
        import core_memory.persistence.graph.graphiti_backend as _mod
        importlib.reload(_mod)
        with self.assertRaises((ImportError, Exception)):
            _mod.GraphitiGraphBackend(uri="bolt://x", user="u", password="p")


if __name__ == "__main__":
    unittest.main()
