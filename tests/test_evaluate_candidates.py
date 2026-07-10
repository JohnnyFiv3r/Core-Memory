import json
import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestEvaluateCandidates(unittest.TestCase):
    def test_evaluate_candidates_writes_shadow_recommendation_only(self):
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
            self.assertTrue(out.get("advisory_only"))
            idx = s._read_json(s.beads_dir / "index.json")
            bead = idx["beads"][bid]
            self.assertNotIn("promotion_recommendation", bead)
            self.assertNotIn("promotion_score", bead)
            shadow_path = s.beads_dir / "events" / "promotion-shadow-recommendations.jsonl"
            rows = [json.loads(line) for line in shadow_path.read_text().splitlines() if line.strip()]
            self.assertTrue(any(row.get("bead_id") == bid for row in rows), rows)


if __name__ == "__main__":
    unittest.main()
