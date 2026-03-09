import tempfile
import unittest

from core_memory.memory_engine import read_live_session
from core_memory.store import MemoryStore


class TestLiveSessionAuthority(unittest.TestCase):
    def test_reads_from_session_surface_first(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="x", summary=["a"], session_id="s1", source_turn_ids=["t1"])

            out = read_live_session(root=td, session_id="s1")
            self.assertEqual("session_surface", out.get("authority"))
            self.assertGreaterEqual(out.get("count", 0), 1)

    def test_index_fallback_when_session_surface_empty(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="g", summary=["x"], session_id="s1", source_turn_ids=["t1"])

            # simulate missing session file while index still has row
            sf = s.beads_dir / "session-s1.jsonl"
            if sf.exists():
                sf.unlink()

            out = read_live_session(root=td, session_id="s1")
            self.assertEqual("index_fallback", out.get("authority"))
            self.assertEqual(1, out.get("count"))


if __name__ == "__main__":
    unittest.main()
