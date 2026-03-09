import tempfile
import unittest

from core_memory.store import MemoryStore


class TestSessionFirstWriteAuthority(unittest.TestCase):
    def test_association_pass_uses_session_surface_not_only_index(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(
                type="context",
                title="candidate promotion decision",
                summary=["promotion workflow"],
                tags=["promotion_workflow"],
                session_id="s1",
                source_turn_ids=["t1"],
            )

            # Simulate stale/missing index projection row while session file still has b1.
            idx = s._read_json(s.beads_dir / "index.json")
            idx.get("beads", {}).pop(b1, None)
            s._write_json(s.beads_dir / "index.json", idx)

            b2 = s.add_bead(
                type="context",
                title="candidate promotion decision followup",
                summary=["promotion workflow"],
                tags=["promotion_workflow"],
                session_id="s1",
                source_turn_ids=["t2"],
            )

            idx2 = s._read_json(s.beads_dir / "index.json")
            preview = (idx2.get("beads", {}).get(b2) or {}).get("association_preview") or []
            self.assertGreaterEqual(len(preview), 1)


if __name__ == "__main__":
    unittest.main()
