from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer_candidates import (
    decide_dreamer_candidate,
    enqueue_dreamer_candidates,
    list_dreamer_candidates,
)


class TestDreamerCandidatesSlice61A(unittest.TestCase):
    def test_enqueue_and_list_candidates(self):
        with tempfile.TemporaryDirectory(prefix="cm-dc-") as td:
            out = enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": "b1",
                        "target": "b2",
                        "relationship": "contradicts",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.75,
                        "source_title": "A",
                        "target_title": "B",
                    }
                ],
                run_metadata={"run_id": "r1", "mode": "suggest", "session_id": "s1"},
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual(1, out.get("added"))

            listed = list_dreamer_candidates(root=td, status="pending", limit=10)
            self.assertTrue(listed.get("ok"))
            self.assertEqual(1, listed.get("count"))
            row = (listed.get("results") or [])[0]
            self.assertEqual("pending", row.get("status"))
            self.assertEqual("contradiction_candidate", row.get("hypothesis_type"))
            self.assertIn("run_metadata", row)
            self.assertIn("expected_decision_impact", row)

    def test_decide_accept_apply_creates_association(self):
        with tempfile.TemporaryDirectory(prefix="cm-dc-") as td:
            store = MemoryStore(td)
            b1 = store.add_bead(type="decision", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b2 = store.add_bead(type="lesson", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

            enq = enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": b1,
                        "target": b2,
                        "relationship": "transferable_lesson",
                        "novelty": 0.7,
                        "grounding": 0.8,
                        "confidence": 0.9,
                    }
                ],
                run_metadata={"run_id": "r2", "mode": "reviewed_apply", "session_id": "s1"},
            )
            cid = None
            rows = list_dreamer_candidates(root=td, status="pending", limit=10).get("results") or []
            if rows:
                cid = rows[0].get("id")
            self.assertTrue(cid)

            dec = decide_dreamer_candidate(
                root=td,
                candidate_id=str(cid),
                decision="accept",
                reviewer="tester",
                notes="looks right",
                apply=True,
            )
            self.assertTrue(dec.get("ok"))
            self.assertEqual("accepted", dec.get("status"))
            self.assertTrue((dec.get("applied") or {}).get("ok"))

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            assocs = idx.get("associations") or []
            self.assertGreaterEqual(len(assocs), 1)


if __name__ == "__main__":
    unittest.main()
