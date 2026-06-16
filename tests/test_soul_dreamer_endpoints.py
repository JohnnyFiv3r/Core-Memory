import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.runtime.dreamer.candidates import _write_candidates
from core_memory.soul.dreamer_bridge import (
    dreamer_soul_findings,
    dreamer_soul_review,
    propose_soul_from_dreamer,
)
from core_memory.soul.store import approve_soul_update


def _tension(cid, key):
    return {"id": cid, "status": "pending", "hypothesis_type": "tension_candidate",
            "tension_key": key, "statement": f"Goals conflict: {key}.",
            "supporting_bead_ids": ["b1"]}


class TestDreamerSoulEndpoints(unittest.TestCase):
    def test_findings_lists_eligible_pending(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [_tension("dc-1", "k1"),
                                   {"id": "dc-2", "status": "pending",
                                    "hypothesis_type": "entity_merge_candidate"}])
            out = dreamer_soul_findings(td)
            self.assertEqual(1, out["count"])
            self.assertEqual("TENSIONS.md", out["findings"][0]["target_file"])
            self.assertEqual("tension:k1", out["findings"][0]["entry_key"])

    def test_review_lists_pending_proposals_then_clears_on_decision(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [_tension("dc-1", "k1")])
            propose_soul_from_dreamer(td)
            review = dreamer_soul_review(td)
            self.assertEqual(1, review["count"])
            rid = review["proposals"][0]["revision_id"]
            approve_soul_update(td, revision_id=rid, approver="human")
            # Decided proposals drop out of the review queue.
            self.assertEqual(0, dreamer_soul_review(td)["count"])

    def test_subject_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            cand = _tension("dc-1", "k1")
            cand["subject"] = "acme"
            _write_candidates(td, [cand])
            self.assertEqual(0, dreamer_soul_findings(td, subject="self")["count"])
            self.assertEqual(1, dreamer_soul_findings(td, subject="acme")["count"])


if __name__ == "__main__":
    unittest.main()
