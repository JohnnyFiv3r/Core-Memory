from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreRelationshipOpsDelegationSlice74A(unittest.TestCase):
    def test_promote_link_recall_delegate(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-rel-deleg-") as td:
            store = MemoryStore(td)

            with patch("core_memory.persistence.store_relationship_ops.promote_for_store", return_value=True) as stub_promote:
                out_promote = store.promote("bead-1", promotion_reason="why")
            self.assertTrue(out_promote)
            self.assertEqual(1, stub_promote.call_count)

            with patch("core_memory.persistence.store_relationship_ops.link_for_store", return_value="assoc-1") as stub_link:
                out_link = store.link("a", "b", "supports", explanation="e", confidence=0.9)
            self.assertEqual("assoc-1", out_link)
            self.assertEqual(1, stub_link.call_count)

            with patch("core_memory.persistence.store_relationship_ops.recall_for_store", return_value=True) as stub_recall:
                out_recall = store.recall("bead-1")
            self.assertTrue(out_recall)
            self.assertEqual(1, stub_recall.call_count)

    def test_stats_and_rebuild_delegate(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-rel-deleg-") as td:
            store = MemoryStore(td)
            expected_stats = {"total_beads": 0, "total_associations": 0, "by_type": {}, "by_status": {}}
            with patch("core_memory.persistence.store_relationship_ops.stats_for_store", return_value=expected_stats) as stub_stats:
                out_stats = store.stats()
            self.assertEqual(expected_stats, out_stats)
            self.assertEqual(1, stub_stats.call_count)

            expected_rebuild = {"beads": {}, "associations": [], "stats": {"total_beads": 0, "total_associations": 0}}
            with patch("core_memory.persistence.store_relationship_ops.rebuild_index_for_store", return_value=expected_rebuild) as stub_rebuild:
                out_rebuild = store.rebuild_index()
            self.assertEqual(expected_rebuild, out_rebuild)
            self.assertEqual(1, stub_rebuild.call_count)


if __name__ == "__main__":
    unittest.main()
