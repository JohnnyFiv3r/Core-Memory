import tempfile
import unittest

from core_memory.store import MemoryStore


class TestPhaseBPromotion(unittest.TestCase):
    def test_candidate_needs_reinforcement_to_promote(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="decision",
                title="Adopt deterministic integration port",
                summary=["Use one emit path"],
                because=["reduce fragmentation"],
                detail="Detailed design rationale sufficient for quality gate.",
                status="candidate",
                session_id="main",
                source_turn_ids=["t1"],
            )

            # No reinforcement yet: should not promote during compact promote pass.
            s.compact(session_id="main", promote=True)
            idx = s._read_json(s.beads_dir / "index.json")
            self.assertNotEqual("promoted", idx["beads"][bid]["status"])

            # Add reinforcement via linked outcome, then compact+promote should pass.
            s.add_bead(
                type="outcome",
                title="Integration path validated",
                summary=["Confirmed stable behavior"],
                result="confirmed",
                linked_bead_id=bid,
                detail="Validation result with linked prior decision.",
                status="candidate",
                session_id="main",
                source_turn_ids=["t2"],
            )
            s.compact(session_id="main", promote=True)
            idx2 = s._read_json(s.beads_dir / "index.json")
            self.assertEqual("promoted", idx2["beads"][bid]["status"])
            self.assertIn("promotion_score", idx2["beads"][bid])

    def test_rebalance_promotions_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(
                type="context",
                title="Low signal context",
                summary=["ok"],
                detail="",
                status="promoted",
                session_id="main",
                source_turn_ids=["t1"],
            )
            out = s.rebalance_promotions(apply=False)
            self.assertTrue(out["ok"])
            self.assertIn("demote_candidates", out)


if __name__ == "__main__":
    unittest.main()
