import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestSessionFirstQueryAuthority(unittest.TestCase):
    def test_query_session_id_uses_session_surface_first(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(
                type="context",
                title="session truth",
                summary=["alpha"],
                session_id="s1",
                source_turn_ids=["t1"],
                tags=["x"],
            )

            # remove from index projection to simulate staleness
            idx = s._read_json(s.beads_dir / "index.json")
            idx.get("beads", {}).pop(b1, None)
            s._write_json(s.beads_dir / "index.json", idx)

            out = s.query(session_id="s1", limit=10)
            self.assertGreaterEqual(len(out), 1)
            self.assertEqual("session truth", out[0].get("title"))


if __name__ == "__main__":
    unittest.main()
