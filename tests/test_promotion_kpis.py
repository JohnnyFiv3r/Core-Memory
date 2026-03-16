import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestPromotionKpis(unittest.TestCase):
    def test_promotion_kpis_reports_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="decision",
                title="Adopt stable naming",
                summary=["core-memory canonical"],
                because=["clarity"],
                status="candidate",
                detail="Detailed rationale for naming standardization.",
                session_id="main",
                source_turn_ids=["t1"],
            )
            s.decide_promotion(bead_id=bid, decision="promote", reason="foundational naming rule")
            out = s.promotion_kpis(limit=50)
            self.assertTrue(out.get("ok"))
            self.assertGreaterEqual(out.get("decision_count", 0), 1)
            self.assertIn("promote", out.get("by_decision", {}))


if __name__ == "__main__":
    unittest.main()
