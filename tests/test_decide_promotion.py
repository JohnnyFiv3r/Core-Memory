import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore


class TestDecidePromotion(unittest.TestCase):
    def test_promote_requires_reason(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="decision",
                title="Adopt stable adapter API",
                summary=["single integration port"],
                status="candidate",
                session_id="main",
                source_turn_ids=["t1"],
            )
            bad = s.decide_promotion(bead_id=bid, decision="promote", reason="")
            self.assertFalse(bad.get("ok"))
            good = s.decide_promotion(bead_id=bid, decision="promote", reason="agent judged load-bearing")
            self.assertTrue(good.get("ok"))

    def test_archive_decision_writes_audit_log(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="context",
                title="Routine status",
                summary=["ok"],
                status="candidate",
                session_id="main",
                source_turn_ids=["t1"],
            )
            out = s.decide_promotion(bead_id=bid, decision="archive", reason="low signal")
            self.assertTrue(out.get("ok"))
            log = Path(td) / ".beads" / "events" / "promotion-decisions.jsonl"
            self.assertTrue(log.exists())

    def test_bulk_decision(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="decision", title="A", summary=["a"], status="candidate", session_id="main", source_turn_ids=["t1"])
            b2 = s.add_bead(type="context", title="B", summary=["b"], status="candidate", session_id="main", source_turn_ids=["t2"])
            out = s.decide_promotion_bulk([
                {"bead_id": b1, "decision": "keep_candidate"},
                {"bead_id": b2, "decision": "archive", "reason": "noise"},
            ])
            self.assertTrue(out.get("ok"))
            self.assertEqual(2, out.get("applied"))


if __name__ == "__main__":
    unittest.main()
