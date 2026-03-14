import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.session_surface import read_session_surface


class TestSessionSurface(unittest.TestCase):
    def test_reads_session_file_rows(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="a", summary=["x"], session_id="main", source_turn_ids=["t1"])
            s.add_bead(type="context", title="b", summary=["y"], session_id="main", source_turn_ids=["t2"])

            rows = read_session_surface(td, "main")
            self.assertGreaterEqual(len(rows), 2)
            self.assertTrue(all(r.get("session_id") == "main" for r in rows))


if __name__ == "__main__":
    unittest.main()
