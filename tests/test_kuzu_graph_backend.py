from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestKuzuGraphBackend(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="cm-kuzu-")
        self.db_path = Path(self._td.name) / "kuzu"
        from core_memory.persistence.graph.kuzu_backend import KuzuGraphBackend
        self.backend = KuzuGraphBackend(self.db_path)

    def tearDown(self):
        self.backend.close()
        self._td.cleanup()

    def _bead(self, bead_id: str, **kwargs) -> dict:
        return {
            "id": bead_id,
            "type": kwargs.get("type", "lesson"),
            "title": kwargs.get("title", f"Bead {bead_id}"),
            "session_id": kwargs.get("session_id", "sess-1"),
            "created_at": "2026-01-01T00:00:00Z",
            "status": kwargs.get("status", "open"),
        }

    def _assoc(self, src: str, tgt: str, rel_type: str = "caused_by") -> dict:
        return {
            "id": f"assoc-{src}-{tgt}",
            "source_bead": src,
            "target_bead": tgt,
            "relationship": rel_type,
            "confidence": 0.9,
            "created_at": "2026-01-01T00:00:00Z",
        }

    def test_capabilities_graph_traversal_true(self):
        caps = self.backend.capabilities()
        self.assertTrue(caps.graph_traversal)
        self.assertFalse(caps.vector_search)

    def test_health_returns_ok(self):
        h = self.backend.health()
        self.assertTrue(h.get("ok"))
        self.assertEqual(h.get("backend"), "kuzu")

    def test_bead_write_and_re_open_idempotent(self):
        bead = self._bead("b1")
        self.backend.on_bead_written(bead)
        self.backend.on_bead_written(bead)  # second write must not raise
        h = self.backend.health()
        self.assertEqual(h.get("bead_count"), 1)

    def test_bead_write_multiple(self):
        for i in range(5):
            self.backend.on_bead_written(self._bead(f"b{i}"))
        self.assertEqual(self.backend.health().get("bead_count"), 5)

    def test_association_write(self):
        self.backend.on_bead_written(self._bead("src"))
        self.backend.on_bead_written(self._bead("tgt"))
        self.backend.on_association_written(self._assoc("src", "tgt"))
        chains = self.backend.traverse(seed_ids=["src"], edge_types=None, max_hops=1)
        self.assertEqual(len(chains), 1)
        nodes = chains[0]["nodes"]
        node_ids = [n["id"] for n in nodes]
        self.assertIn("tgt", node_ids)

    def test_1hop_traversal(self):
        self.backend.on_bead_written(self._bead("a"))
        self.backend.on_bead_written(self._bead("b"))
        self.backend.on_association_written(self._assoc("a", "b", "caused_by"))
        chains = self.backend.traverse(seed_ids=["a"], edge_types=None, max_hops=1)
        self.assertEqual(len(chains), 1)
        edge = chains[0]["edges"][0]
        self.assertEqual(edge["rel"], "caused_by")

    def test_3hop_traversal(self):
        for bid in ("x", "y", "z", "w"):
            self.backend.on_bead_written(self._bead(bid))
        self.backend.on_association_written(self._assoc("x", "y"))
        self.backend.on_association_written(self._assoc("y", "z"))
        self.backend.on_association_written(self._assoc("z", "w"))
        chains = self.backend.traverse(seed_ids=["x"], edge_types=None, max_hops=3)
        reached_ids = {n["id"] for c in chains for n in c["nodes"]}
        self.assertIn("w", reached_ids)

    def test_edge_type_filter(self):
        self.backend.on_bead_written(self._bead("a"))
        self.backend.on_bead_written(self._bead("b"))
        self.backend.on_bead_written(self._bead("c"))
        self.backend.on_association_written(self._assoc("a", "b", "caused_by"))
        self.backend.on_association_written(self._assoc("a", "c", "follows"))
        chains_all = self.backend.traverse(seed_ids=["a"], edge_types=None, max_hops=1)
        chains_filtered = self.backend.traverse(seed_ids=["a"], edge_types=["caused_by"], max_hops=1)
        self.assertEqual(len(chains_all), 2)
        self.assertEqual(len(chains_filtered), 1)
        self.assertEqual(chains_filtered[0]["edges"][0]["rel"], "caused_by")

    def test_retracted_node_excluded_from_traversal(self):
        self.backend.on_bead_written(self._bead("a"))
        self.backend.on_bead_written(self._bead("b"))
        self.backend.on_association_written(self._assoc("a", "b"))
        self.backend.on_bead_retracted("b")
        chains = self.backend.traverse(seed_ids=["a"], edge_types=None, max_hops=1)
        for chain in chains:
            for node in chain["nodes"]:
                self.assertNotEqual(node["id"], "b")

    def test_empty_graph_returns_empty_chains(self):
        chains = self.backend.traverse(seed_ids=["nonexistent"], edge_types=None, max_hops=3)
        self.assertEqual(chains, [])

    def test_empty_seed_ids_returns_empty(self):
        chains = self.backend.traverse(seed_ids=[], edge_types=None, max_hops=3)
        self.assertEqual(chains, [])

    def test_schema_idempotent_on_reopen(self):
        """Opening an existing Kuzu DB at same path must not fail."""
        self.backend.close()
        from core_memory.persistence.graph.kuzu_backend import KuzuGraphBackend
        backend2 = KuzuGraphBackend(self.db_path)
        h = backend2.health()
        self.assertTrue(h.get("ok"))
        backend2.close()

    def test_sync_from_storage(self):
        beads = [self._bead(f"b{i}") for i in range(3)]
        assocs = [self._assoc("b0", "b1"), self._assoc("b1", "b2")]
        result = self.backend.sync_from_storage(beads, assocs)
        self.assertEqual(result["synced_beads"], 3)
        self.assertEqual(result["synced_associations"], 2)
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
