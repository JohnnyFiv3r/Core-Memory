from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer_candidates import decide_dreamer_candidate, enqueue_dreamer_candidates, list_dreamer_candidates
from core_memory.runtime.dreamer_eval import dreamer_eval_report


class TestDreamerEvalSlice64A(unittest.TestCase):
    def _seed_store(self, root: str) -> tuple[str, str, str]:
        s = MemoryStore(root)
        b1 = s.add_bead(type="decision", title="Retry policy", summary=["always stage"], session_id="s1", source_turn_ids=["t1"], incident_keys=["api-timeout"])
        b2 = s.add_bead(type="lesson", title="Cross-team lesson", summary=["transfer lesson"], session_id="s2", source_turn_ids=["t2"], incident_keys=["api-timeout"])
        b3 = s.add_bead(type="outcome", title="Incident outcome", summary=["failed rollout"], session_id="s1", source_turn_ids=["t3"], incident_keys=["api-timeout"])
        return b1, b2, b3

    def test_dreamer_eval_report_metrics(self):
        with tempfile.TemporaryDirectory(prefix="cm-dream-eval-") as td:
            b1, b2, b3 = self._seed_store(td)

            enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": b1,
                        "target": b2,
                        "relationship": "transferable_lesson",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.8,
                        "structural_signals": [{"name": "transferability_cross_scope", "weight": 0.2}],
                    },
                    {
                        "source": b1,
                        "target": b3,
                        "relationship": "contradicts",
                        "novelty": 0.7,
                        "grounding": 0.8,
                        "confidence": 0.7,
                        "structural_signals": [{"name": "repeated_incident", "weight": 0.2}],
                    },
                ],
                run_metadata={"run_id": "r1", "mode": "suggest", "session_id": "s1"},
            )

            pending = list_dreamer_candidates(root=td, status="pending", limit=10).get("results") or []
            self.assertEqual(2, len(pending))

            c_transfer = next(c for c in pending if str(c.get("relationship") or "") == "transferable_lesson")
            c_contra = next(c for c in pending if str(c.get("relationship") or "") == "contradicts")

            dec1 = decide_dreamer_candidate(root=td, candidate_id=str(c_transfer.get("id")), decision="accept", reviewer="qa", apply=True)
            self.assertTrue(dec1.get("ok"))
            dec2 = decide_dreamer_candidate(root=td, candidate_id=str(c_contra.get("id")), decision="reject", reviewer="qa")
            self.assertTrue(dec2.get("ok"))

            # downstream use proxy via recall_count
            s = MemoryStore(td)
            s.recall(b1)

            out = dreamer_eval_report(td, since="30d")
            self.assertEqual("core_memory.dreamer_eval.v1", out.get("schema"))
            counts = out.get("counts") or {}
            metrics = out.get("metrics") or {}

            self.assertEqual(2, int(counts.get("total_candidates") or 0))
            self.assertEqual(2, int(counts.get("decided") or 0))
            self.assertEqual(1, int(counts.get("accepted") or 0))
            self.assertEqual(1, int(counts.get("rejected") or 0))

            self.assertAlmostEqual(0.5, float(metrics.get("accepted_candidate_rate") or 0.0), places=3)
            self.assertAlmostEqual(1.0, float(metrics.get("cross_session_transfer_success_rate") or 0.0), places=3)
            self.assertAlmostEqual(0.0, float(metrics.get("repeated_mistake_reduction_proxy") or 0.0), places=3)
            self.assertGreaterEqual(float(metrics.get("downstream_retrieval_use_rate_of_accepted_outputs") or 0.0), 1.0)

    def test_since_window_filters_old_candidates(self):
        with tempfile.TemporaryDirectory(prefix="cm-dream-eval-") as td:
            b1, b2, _b3 = self._seed_store(td)
            enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": b1,
                        "target": b2,
                        "relationship": "transferable_lesson",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.8,
                    }
                ],
                run_metadata={"run_id": "r1", "mode": "suggest", "session_id": "s1"},
            )

            p = Path(td) / ".beads" / "events" / "dreamer-candidates.json"
            rows = json.loads(p.read_text(encoding="utf-8"))
            rows[0]["created_at"] = "2000-01-01T00:00:00+00:00"
            p.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

            out = dreamer_eval_report(td, since="1d")
            self.assertEqual(0, int((out.get("counts") or {}).get("total_candidates") or 0))


if __name__ == "__main__":
    unittest.main()
