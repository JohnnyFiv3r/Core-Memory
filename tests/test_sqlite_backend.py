import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.backend import SqliteBackend


class TestSqliteBackend(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.beads_dir = Path(self.td) / ".beads"
        self.beads_dir.mkdir(parents=True)
        self.backend = SqliteBackend(self.beads_dir)

    def test_put_and_get_bead(self):
        bead = {"id": "bead-001", "type": "decision", "status": "open", "title": "Test", "session_id": "s1", "created_at": "2026-01-01T00:00:00Z"}
        self.backend.put_bead(bead)
        got = self.backend.get_bead("bead-001")
        self.assertIsNotNone(got)
        self.assertEqual(got["title"], "Test")
        self.assertEqual(got["type"], "decision")

    def test_get_missing_bead(self):
        self.assertIsNone(self.backend.get_bead("nonexistent"))

    def test_delete_bead(self):
        bead = {"id": "bead-002", "type": "goal", "status": "open", "title": "Goal", "session_id": "s1", "created_at": "2026-01-01T00:00:00Z"}
        self.backend.put_bead(bead)
        self.assertTrue(self.backend.delete_bead("bead-002"))
        self.assertIsNone(self.backend.get_bead("bead-002"))
        self.assertFalse(self.backend.delete_bead("bead-002"))

    def test_query_beads_by_type(self):
        self.backend.put_bead({"id": "b1", "type": "decision", "status": "open", "session_id": "s1", "created_at": "2026-01-01"})
        self.backend.put_bead({"id": "b2", "type": "goal", "status": "open", "session_id": "s1", "created_at": "2026-01-02"})
        self.backend.put_bead({"id": "b3", "type": "decision", "status": "promoted", "session_id": "s1", "created_at": "2026-01-03"})

        decisions = self.backend.query_beads({"type": "decision"})
        self.assertEqual(len(decisions), 2)
        goals = self.backend.query_beads({"type": "goal"})
        self.assertEqual(len(goals), 1)

    def test_query_beads_by_status(self):
        self.backend.put_bead({"id": "b1", "type": "decision", "status": "open", "session_id": "s1", "created_at": "2026-01-01"})
        self.backend.put_bead({"id": "b2", "type": "decision", "status": "promoted", "session_id": "s1", "created_at": "2026-01-02"})

        promoted = self.backend.query_beads({"status": "promoted"})
        self.assertEqual(len(promoted), 1)

    def test_associations(self):
        assoc = {"source_bead": "b1", "target_bead": "b2", "relationship": "caused_by", "weight": 1.0}
        self.backend.put_association(assoc)
        all_assocs = self.backend.get_associations()
        self.assertEqual(len(all_assocs), 1)
        self.assertEqual(all_assocs[0]["source_bead"], "b1")

    def test_associations_for_bead(self):
        self.backend.put_association({"source_bead": "b1", "target_bead": "b2", "relationship": "caused_by"})
        self.backend.put_association({"source_bead": "b3", "target_bead": "b1", "relationship": "supports"})
        self.backend.put_association({"source_bead": "b4", "target_bead": "b5", "relationship": "led_to"})

        b1_assocs = self.backend.get_associations_for_bead("b1")
        self.assertEqual(len(b1_assocs), 2)

    def test_save_and_load_full_index(self):
        index = {
            "beads": {
                "b1": {"id": "b1", "type": "decision", "status": "open", "session_id": "s1", "created_at": "2026-01-01"},
                "b2": {"id": "b2", "type": "goal", "status": "promoted", "session_id": "s1", "created_at": "2026-01-02"},
            },
            "associations": [
                {"source_bead": "b1", "target_bead": "b2", "relationship": "led_to"},
            ],
            "stats": {"total_beads": 2, "total_associations": 1, "created_at": "2026-01-01"},
            "projection": {"mode": "session_first_projection_cache", "rebuilt_at": "2026-01-01"},
        }
        self.backend.save_index(index)
        loaded = self.backend.load_index()

        self.assertEqual(len(loaded["beads"]), 2)
        self.assertEqual(len(loaded["associations"]), 1)
        self.assertEqual(loaded["stats"]["total_beads"], 2)

    def test_cache_invalidation(self):
        self.backend.put_bead({"id": "b1", "type": "decision", "status": "open", "session_id": "s1", "created_at": "2026-01-01"})
        idx1 = self.backend.load_index()
        self.assertEqual(len(idx1["beads"]), 1)

        self.backend.put_bead({"id": "b2", "type": "goal", "status": "open", "session_id": "s2", "created_at": "2026-01-02"})
        idx2 = self.backend.load_index()
        self.assertEqual(len(idx2["beads"]), 2)

    def test_get_stats(self):
        self.backend.put_bead({"id": "b1", "type": "decision", "status": "open", "session_id": "s1", "created_at": "2026-01-01"})
        self.backend.put_association({"source_bead": "b1", "target_bead": "b2", "relationship": "caused_by"})
        stats = self.backend.get_stats()
        self.assertEqual(stats["total_beads"], 1)
        self.assertEqual(stats["total_associations"], 1)


if __name__ == "__main__":
    unittest.main()
