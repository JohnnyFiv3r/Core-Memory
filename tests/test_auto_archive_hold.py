import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestAutoArchiveHold(unittest.TestCase):
    def test_auto_archive_hold_candidate_is_advisory_only(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="context",
                title="Low signal note",
                summary=["ok"],
                status="candidate",
                session_id="main",
                source_turn_ids=["t1"],
            )
            out = s.evaluate_candidates(limit=50, query_text="", auto_archive_hold=True, min_age_hours=0)
            self.assertTrue(out.get("ok"))
            self.assertEqual(0, out.get("auto_archived", 0))
            self.assertGreaterEqual(out.get("advisory_archive_candidates", 0), 1)
            idx = s._read_json(s.beads_dir / "index.json")
            self.assertEqual("candidate", idx["beads"][bid]["status"])


if __name__ == "__main__":
    unittest.main()
