import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestPromotionSlate(unittest.TestCase):
    def test_promotion_slate_returns_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(
                type="context",
                title="Core-Memory naming transition",
                summary=["Name moved from mem-beads to core-memory"],
                status="candidate",
                because=["important naming continuity"],
                session_id="main",
                source_turn_ids=["t1", "t2"],
            )
            out = s.promotion_slate(limit=10, query_text="name before core memory")
            self.assertTrue(out.get("ok"))
            self.assertGreaterEqual(out.get("candidate_total", 0), 1)
            self.assertTrue(len(out.get("results") or []) >= 1)
            self.assertIn("recommendation", out["results"][0])


if __name__ == "__main__":
    unittest.main()
