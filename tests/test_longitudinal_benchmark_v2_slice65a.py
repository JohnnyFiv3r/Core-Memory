from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer_candidates import decide_dreamer_candidate, enqueue_dreamer_candidates, list_dreamer_candidates
from core_memory.runtime.longitudinal_benchmark import longitudinal_benchmark_v2


class TestLongitudinalBenchmarkV2Slice65A(unittest.TestCase):
    def _seed(self, root: str) -> tuple[str, str, str]:
        s = MemoryStore(root)
        b1 = s.add_bead(
            type="decision",
            title="Rollout policy",
            summary=["use canary"],
            session_id="s1",
            source_turn_ids=["t1"],
            incident_keys=["deploy-risk"],
        )
        b2 = s.add_bead(
            type="lesson",
            title="Cross-session lesson",
            summary=["reuse canary-first lesson"],
            session_id="s2",
            source_turn_ids=["t2"],
            incident_keys=["deploy-risk"],
        )
        b3 = s.add_bead(
            type="outcome",
            title="Same-session note",
            summary=["staged rollout helped"],
            session_id="s1",
            source_turn_ids=["t3"],
        )
        return b1, b2, b3

    def test_longitudinal_benchmark_reports_all_cohorts(self):
        with tempfile.TemporaryDirectory(prefix="cm-long-v2-") as td:
            b1, b2, b3 = self._seed(td)
            enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": b1,
                        "target": b3,
                        "relationship": "reinforces",
                        "novelty": 0.4,
                        "grounding": 0.7,
                        "confidence": 0.6,
                    },
                    {
                        "source": b1,
                        "target": b2,
                        "relationship": "transferable_lesson",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.8,
                        "structural_signals": [{"name": "transferability_cross_scope", "weight": 0.2}],
                    },
                ],
                run_metadata={"run_id": "r1", "mode": "suggest", "session_id": "s1"},
            )

            pending = list_dreamer_candidates(root=td, status="pending", limit=10).get("results") or []
            self.assertEqual(2, len(pending))
            c_summary = next(c for c in pending if str(c.get("relationship") or "") == "reinforces")
            c_struct = next(c for c in pending if str(c.get("relationship") or "") == "transferable_lesson")

            d1 = decide_dreamer_candidate(root=td, candidate_id=str(c_summary.get("id")), decision="accept", reviewer="qa", apply=False)
            self.assertTrue(d1.get("ok"))
            d2 = decide_dreamer_candidate(root=td, candidate_id=str(c_struct.get("id")), decision="accept", reviewer="qa", apply=True)
            self.assertTrue(d2.get("ok"))

            # downstream-use proxy
            s = MemoryStore(td)
            s.recall(b1)

            out = longitudinal_benchmark_v2(td, since="30d")
            self.assertEqual("core_memory.longitudinal_benchmark_v2.v1", out.get("schema"))

            cohorts = out.get("cohorts") or {}
            self.assertIn("no_memory_baseline", cohorts)
            self.assertIn("summary_only_baseline", cohorts)
            self.assertIn("core_memory_without_dreamer", cohorts)
            self.assertIn("core_memory_with_dreamer", cohorts)

            comparisons = out.get("comparisons") or {}
            self.assertGreater(float(comparisons.get("core_with_dreamer_vs_no_memory_lift") or 0.0), 0.0)

    def test_since_filters_old_rows(self):
        with tempfile.TemporaryDirectory(prefix="cm-long-v2-") as td:
            b1, b2, _b3 = self._seed(td)
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
                    },
                ],
                run_metadata={"run_id": "r1", "mode": "suggest", "session_id": "s1"},
            )

            p = Path(td) / ".beads" / "events" / "dreamer-candidates.json"
            rows = json.loads(p.read_text(encoding="utf-8"))
            rows[0]["created_at"] = "2000-01-01T00:00:00+00:00"
            p.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

            out = longitudinal_benchmark_v2(td, since="1d")
            self.assertEqual(0, int((out.get("diagnostics") or {}).get("total_candidates_scoped") or 0))


if __name__ == "__main__":
    unittest.main()
