import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestRequiredFieldsV2(unittest.TestCase):
    def test_warnings_attached_when_not_strict(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bead_id = s.add_bead(
                type="lesson",
                title="Weak lesson bead",
                summary=["short"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            bead = s._read_json(s.beads_dir / "index.json")["beads"][bead_id]
            self.assertIn("validation_warnings", bead)
            self.assertIn("lesson:missing_because", bead["validation_warnings"])

    def test_decision_promotion_gate_requires_because_and_evidence_or_detail(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="decision",
                title="Switch adapter path",
                summary=["Use integration port"],
                session_id="main",
                source_turn_ids=["t1"],
                because=["Unified integrations"],
            )
            # no detail/evidence => should fail promotion
            self.assertFalse(s.promote(bid, promotion_reason="test"))

            # add detail then promote
            idx = s._read_json(s.beads_dir / "index.json")
            idx["beads"][bid]["detail"] = "Detailed decision rationale and tradeoffs"
            s._write_json(s.beads_dir / "index.json", idx)
            self.assertTrue(s.promote(bid, promotion_reason="foundational design rule"))
            idx2 = s._read_json(s.beads_dir / "index.json")
            self.assertEqual("promoted", idx2["beads"][bid]["status"])
            self.assertEqual("foundational design rule", idx2["beads"][bid]["promotion_reason"])


if __name__ == "__main__":
    unittest.main()
