import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.archive_index import append_archive_snapshot, read_snapshot, rebuild_archive_index
from core_memory.store import MemoryStore


class TestArchiveIndex(unittest.TestCase):
    def test_append_and_read_snapshot_o1_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            row = {
                "bead_id": "bead-1",
                "revision_id": "rev-1",
                "archived_at": "2026-01-01T00:00:00Z",
                "snapshot": {"id": "bead-1", "title": "x"},
            }
            meta = append_archive_snapshot(root, row)
            self.assertEqual("rev-1", meta["revision_id"])

            found = read_snapshot(root, "rev-1")
            self.assertIsNotNone(found)
            self.assertEqual("bead-1", found.get("bead_id"))

            idx = json.loads((root / ".beads" / "archive_index.json").read_text(encoding="utf-8"))
            self.assertIn("rev-1", idx)
            self.assertGreaterEqual(int(idx["rev-1"].get("length", 0)), 1)

    def test_uncompact_uses_revision_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="decision",
                title="T",
                summary=["s1", "s2"],
                detail="full detail",
                because=["b"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            s.compact(session_id="main", promote=False)
            out = s.uncompact(bid)
            self.assertTrue(out.get("ok"))
            idx = s._read_json(s.beads_dir / "index.json")
            self.assertIn("full detail", idx["beads"][bid].get("detail", ""))

    def test_rebuild_archive_index(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            append_archive_snapshot(root, {"bead_id": "bead-1", "revision_id": "rev-a", "archived_at": "x", "snapshot": {"id": "bead-1"}})
            (root / ".beads" / "archive_index.json").unlink()
            res = rebuild_archive_index(root)
            self.assertTrue(res.get("ok"))
            self.assertEqual(1, res.get("entries"))


if __name__ == "__main__":
    unittest.main()
