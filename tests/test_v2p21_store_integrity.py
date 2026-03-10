import tempfile
import unittest

from core_memory.store import MemoryStore


class TestV2P21StoreIntegrity(unittest.TestCase):
    def test_add_bead_rejects_reserved_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            with self.assertRaises(ValueError):
                s.add_bead(
                    type="context",
                    title="t",
                    summary=["s"],
                    id="BEAD-OVERRIDE",
                )

    def test_failure_signature_ranking_prefers_overlap_then_recency(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            # highest overlap candidate
            s.add_bead(
                type="failed_hypothesis",
                title="high overlap",
                summary=["x"],
                tags=["alpha", "beta"],
                session_id="s1",
                source_turn_ids=["t1"],
            )
            # lower overlap
            s.add_bead(
                type="failed_hypothesis",
                title="low overlap",
                summary=["x"],
                tags=["alpha"],
                session_id="s1",
                source_turn_ids=["t2"],
            )
            # zero overlap but newer insertion
            s.add_bead(
                type="failed_hypothesis",
                title="zero overlap newer",
                summary=["x"],
                tags=["zzz"],
                session_id="s1",
                source_turn_ids=["t3"],
            )

            out = s.find_failure_signature_matches(tags=["alpha", "beta"], limit=3)
            self.assertGreaterEqual(len(out), 2)
            self.assertGreaterEqual(out[0]["tag_overlap"], out[1]["tag_overlap"])
            self.assertNotEqual("zero overlap newer", out[0].get("title"))


if __name__ == "__main__":
    unittest.main()
