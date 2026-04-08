from __future__ import annotations

import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestLifecycleStatusSplitSlice97A(unittest.TestCase):
    def test_new_beads_use_default_status_and_null_promotion_state(self):
        with tempfile.TemporaryDirectory(prefix="cm-life-split-") as td:
            s = MemoryStore(td)
            bid = s.add_bead(type="lesson", title="t", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            row = s.query(limit=10)[0]
            self.assertEqual(bid, row.get("id"))
            self.assertEqual("default", str(row.get("status") or ""))
            self.assertIsNone(row.get("promotion_state"))

    def test_promotion_decisions_change_promotion_state_not_storage_status(self):
        with tempfile.TemporaryDirectory(prefix="cm-life-split-") as td:
            s = MemoryStore(td)
            bid = s.add_bead(type="lesson", title="t", summary=["x"], because=["why"], session_id="s1", source_turn_ids=["t1"])

            c = s.decide_promotion(bead_id=bid, decision="candidate", reason="review")
            self.assertTrue(c.get("ok"))
            b = s.query(limit=10)[0]
            self.assertEqual("default", str(b.get("status") or ""))
            self.assertEqual("candidate", str(b.get("promotion_state") or ""))

            p = s.decide_promotion(bead_id=bid, decision="promote", reason="approve")
            self.assertTrue(p.get("ok"))
            b2 = s.query(limit=10)[0]
            self.assertEqual("default", str(b2.get("status") or ""))
            self.assertEqual("promoted", str(b2.get("promotion_state") or ""))


if __name__ == "__main__":
    unittest.main()
