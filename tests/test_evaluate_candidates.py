import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestEvaluateCandidates(unittest.TestCase):
    def test_evaluate_candidates_updates_advisory_fields(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="decision",
                title="Adopt core-memory naming",
                summary=["rename canonical package"],
                because=["clarity"],
                status="candidate",
                session_id="main",
                source_turn_ids=["t1"],
            )
            out = s.evaluate_candidates(limit=50, query_text="naming")
            self.assertTrue(out.get("ok"))
            idx = s._read_json(s.beads_dir / "index.json")
            bead = idx["beads"][bid]
            self.assertIn("promotion_recommendation", bead)
            self.assertIn("promotion_score", bead)


if __name__ == "__main__":
    unittest.main()
