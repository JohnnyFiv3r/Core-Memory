import tempfile
import unittest

from core_memory.store import MemoryStore


class TestIndexProjectionCache(unittest.TestCase):
    def test_rebuild_projection_from_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            _b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

            idx = s._read_json(s.beads_dir / "index.json")
            idx["beads"] = {}
            s._write_json(s.beads_dir / "index.json", idx)

            out = s.rebuild_index_projection_from_sessions()
            self.assertTrue(out.get("ok"))
            self.assertEqual("session_first_projection_cache", out.get("mode"))

            idx2 = s._read_json(s.beads_dir / "index.json")
            self.assertIn(b1, idx2.get("beads", {}))
            self.assertEqual("session_first_projection_cache", (idx2.get("projection") or {}).get("mode"))


if __name__ == "__main__":
    unittest.main()
