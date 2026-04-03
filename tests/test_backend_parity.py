"""Verify that JsonFileBackend and SqliteBackend produce identical results."""
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.backend import JsonFileBackend, SqliteBackend


def _make_test_beads():
    return [
        {"id": "b1", "type": "decision", "status": "open", "title": "Choose PostgreSQL",
         "summary": ["JSONB support", "CTE queries"], "tags": ["database"],
         "session_id": "s1", "created_at": "2026-01-01T00:00:00Z"},
        {"id": "b2", "type": "outcome", "status": "promoted", "title": "Migration succeeded",
         "summary": ["Zero downtime"], "tags": ["database", "migration"],
         "session_id": "s1", "created_at": "2026-01-02T00:00:00Z", "promoted_at": "2026-01-03T00:00:00Z"},
        {"id": "b3", "type": "goal", "status": "open", "title": "Reduce query latency",
         "summary": ["Target < 50ms"], "tags": ["performance"],
         "session_id": "s2", "created_at": "2026-01-03T00:00:00Z"},
    ]


def _make_test_assocs():
    return [
        {"source_bead": "b1", "target_bead": "b2", "relationship": "led_to", "weight": 1.0},
        {"source_bead": "b2", "target_bead": "b3", "relationship": "supports", "weight": 0.8},
    ]


class TestBackendParity(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        json_dir = Path(self.td) / "json" / ".beads"
        json_dir.mkdir(parents=True)
        sqlite_dir = Path(self.td) / "sqlite" / ".beads"
        sqlite_dir.mkdir(parents=True)
        self.json_backend = JsonFileBackend(json_dir)
        self.sqlite_backend = SqliteBackend(sqlite_dir)

    def _populate_both(self):
        for bead in _make_test_beads():
            self.json_backend.put_bead(bead)
            self.sqlite_backend.put_bead(bead)
        for assoc in _make_test_assocs():
            self.json_backend.put_association(assoc)
            self.sqlite_backend.put_association(assoc)

    def test_get_bead_parity(self):
        self._populate_both()
        for bid in ["b1", "b2", "b3", "nonexistent"]:
            j = self.json_backend.get_bead(bid)
            s = self.sqlite_backend.get_bead(bid)
            self.assertEqual(j, s, f"Mismatch for bead {bid}")

    def test_delete_bead_parity(self):
        self._populate_both()
        self.assertEqual(
            self.json_backend.delete_bead("b2"),
            self.sqlite_backend.delete_bead("b2"),
        )
        self.assertIsNone(self.json_backend.get_bead("b2"))
        self.assertIsNone(self.sqlite_backend.get_bead("b2"))

    def test_query_by_type_parity(self):
        self._populate_both()
        j = sorted([b["id"] for b in self.json_backend.query_beads({"type": "decision"})])
        s = sorted([b["id"] for b in self.sqlite_backend.query_beads({"type": "decision"})])
        self.assertEqual(j, s)

    def test_query_by_status_parity(self):
        self._populate_both()
        j = sorted([b["id"] for b in self.json_backend.query_beads({"status": "open"})])
        s = sorted([b["id"] for b in self.sqlite_backend.query_beads({"status": "open"})])
        self.assertEqual(j, s)

    def test_associations_parity(self):
        self._populate_both()
        j = self.json_backend.get_associations()
        s = self.sqlite_backend.get_associations()
        self.assertEqual(len(j), len(s))

    def test_associations_for_bead_parity(self):
        self._populate_both()
        j = self.json_backend.get_associations_for_bead("b2")
        s = self.sqlite_backend.get_associations_for_bead("b2")
        self.assertEqual(len(j), len(s))

    def test_full_index_roundtrip_parity(self):
        """Save a full index to both backends, reload, compare."""
        index = {
            "beads": {b["id"]: b for b in _make_test_beads()},
            "associations": _make_test_assocs(),
            "stats": {"total_beads": 3, "total_associations": 2, "created_at": "2026-01-01"},
            "projection": {"mode": "session_first_projection_cache", "rebuilt_at": "2026-01-01"},
        }
        self.json_backend.save_index(index)
        self.sqlite_backend.save_index(index)

        j = self.json_backend.load_index()
        s = self.sqlite_backend.load_index()

        self.assertEqual(sorted(j["beads"].keys()), sorted(s["beads"].keys()))
        self.assertEqual(len(j["associations"]), len(s["associations"]))
        self.assertEqual(j["stats"]["total_beads"], s["stats"]["total_beads"])

    def test_memorystore_with_sqlite_backend(self):
        """MemoryStore works with SQLite backend end-to-end."""
        from core_memory.persistence.store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            store = MemoryStore(root, backend="sqlite")
            bid = store.add_bead(
                type="decision",
                title="SQLite test",
                summary=["Backend parity"],
                session_id="test",
                source_turn_ids=["t1"],
            )
            self.assertTrue(bid.startswith("bead-"))
            results = store.query(session_id="test", limit=5)
            self.assertTrue(len(results) >= 1)
            titles = [r.get("title") for r in results]
            self.assertIn("SQLite test", titles)


if __name__ == "__main__":
    unittest.main()
