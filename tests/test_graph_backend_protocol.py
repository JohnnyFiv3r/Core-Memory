"""Phase 7a: NullGraphBackend satisfies the GraphBackend protocol contract."""
from __future__ import annotations

import unittest

from core_memory.persistence.backend import BackendCapabilities
from core_memory.persistence.graph import GraphBackend, NullGraphBackend


class TestNullGraphBackendProtocol(unittest.TestCase):
    def setUp(self):
        self.backend = NullGraphBackend()

    def test_name_is_null(self):
        self.assertEqual("null", self.backend.name)

    def test_capabilities_all_false(self):
        caps = self.backend.capabilities()
        self.assertIsInstance(caps, BackendCapabilities)
        self.assertFalse(caps.vector_search)
        self.assertFalse(caps.graph_traversal)
        self.assertFalse(caps.full_text_search)
        self.assertFalse(caps.transcript_hydration)

    def test_health_returns_ok(self):
        h = self.backend.health()
        self.assertTrue(h.get("ok"))

    def test_traverse_returns_empty(self):
        result = self.backend.traverse(seed_ids=["x"], edge_types=None, max_hops=3)
        self.assertEqual([], result)

    def test_on_bead_written_is_noop(self):
        # Must not raise
        self.backend.on_bead_written({"id": "b1", "type": "decision"})

    def test_on_association_written_is_noop(self):
        self.backend.on_association_written({"source_bead": "b1", "target_bead": "b2"})

    def test_on_bead_retracted_is_noop(self):
        self.backend.on_bead_retracted("b1")

    def test_sync_from_storage_returns_zero_counts(self):
        result = self.backend.sync_from_storage([], [])
        self.assertIn("synced_beads", result)
        self.assertIn("synced_associations", result)
        self.assertEqual(0, result["synced_beads"])
        self.assertEqual(0, result["synced_associations"])

    def test_close_does_not_raise(self):
        self.backend.close()

    def test_implements_graph_backend_protocol(self):
        self.assertIsInstance(self.backend, GraphBackend)


if __name__ == "__main__":
    unittest.main()
