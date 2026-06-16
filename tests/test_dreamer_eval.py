from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate, enqueue_dreamer_candidates, list_dreamer_candidates
from core_memory.runtime.dreamer.eval import append_dreamer_eval_label, dreamer_eval_report, read_dreamer_eval_labels


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
            self.assertGreaterEqual(len(pending), 2)

            c_transfer = next(c for c in pending if str(c.get("relationship_signal") or "") == "transferable_lesson")
            c_contra = next(c for c in pending if str(c.get("relationship") or "") == "contradicts")
            self.assertEqual("applies_pattern_of", c_transfer.get("relationship"))

            dec1 = decide_dreamer_candidate(root=td, candidate_id=str(c_transfer.get("id")), decision="accept", reviewer="qa", apply=True)
            self.assertTrue(dec1.get("ok"))
            self.assertTrue(str((((dec1.get("applied") or {}) if isinstance(dec1, dict) else {}).get("turn_id") or "")))
            dec2 = decide_dreamer_candidate(root=td, candidate_id=str(c_contra.get("id")), decision="reject", reviewer="qa")
            self.assertTrue(dec2.get("ok"))

            # downstream use proxy via recall_count
            s = MemoryStore(td)
            s.recall(b1)

            out = dreamer_eval_report(td, since="30d")
            self.assertEqual("core_memory.dreamer_eval.v1", out.get("schema"))
            counts = out.get("counts") or {}
            metrics = out.get("metrics") or {}

            self.assertGreaterEqual(int(counts.get("total_candidates") or 0), 2)
            self.assertEqual(2, int(counts.get("decided") or 0))
            self.assertEqual(1, int(counts.get("accepted") or 0))
            self.assertEqual(1, int(counts.get("rejected") or 0))
            self.assertEqual(1, int(counts.get("accepted_applied") or 0))

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
            for r in rows:
                if isinstance(r, dict):
                    r["created_at"] = "2000-01-01T00:00:00+00:00"
            p.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

            out = dreamer_eval_report(td, since="1d")
            self.assertEqual(0, int((out.get("counts") or {}).get("total_candidates") or 0))

    def test_human_labels_report_precision_actionability_and_samples(self):
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
                    {
                        "source": b2,
                        "target": b3,
                        "relationship": "supports",
                        "novelty": 0.5,
                        "grounding": 0.7,
                        "confidence": 0.6,
                    },
                ],
                run_metadata={"run_id": "r1", "mode": "suggest", "session_id": "s1"},
            )
            pending = list_dreamer_candidates(root=td, status="pending", limit=10).get("results") or []
            by_signal = {str(c.get("relationship_signal") or c.get("relationship") or ""): c for c in pending}
            c_transfer = by_signal["transferable_lesson"]
            c_contra = by_signal["contradicts"]

            self.assertTrue(append_dreamer_eval_label(
                td,
                candidate_id=str(c_transfer["id"]),
                label="true_positive",
                actionable=True,
                reviewer="qa",
            )["ok"])
            self.assertTrue(append_dreamer_eval_label(
                td,
                candidate_id=str(c_contra["id"]),
                label="false_positive",
                actionable=False,
                reviewer="qa",
            )["ok"])
            # Latest label wins for a candidate and unclear is excluded from precision.
            append_dreamer_eval_label(
                td,
                candidate_id=str(c_contra["id"]),
                label="unclear",
                actionable=False,
                reviewer="qa2",
            )

            rows = read_dreamer_eval_labels(td)
            self.assertEqual(3, len(rows))
            out = dreamer_eval_report(td, since="30d", sample_limit=5)
            labels = out["human_labels"]
            transfer_type = str(c_transfer.get("hypothesis_type") or "")
            contra_type = str(c_contra.get("hypothesis_type") or "")

            self.assertEqual(1, labels["counts_by_type"][transfer_type]["true_positive"])
            self.assertEqual(1, labels["counts_by_type"][contra_type]["unclear"])
            self.assertAlmostEqual(1.0, labels["precision_by_type"][transfer_type], places=3)
            self.assertAlmostEqual(0.0, labels["precision_by_type"][contra_type], places=3)
            self.assertAlmostEqual(1.0, labels["actionability_rate_by_type"][transfer_type], places=3)
            samples_a = labels["unlabeled_review_samples"]
            samples_b = dreamer_eval_report(td, since="30d", sample_limit=5)["human_labels"]["unlabeled_review_samples"]
            self.assertEqual(samples_a, samples_b)
            self.assertTrue(any(str(s.get("candidate_id") or "") for s in samples_a))

    def test_label_validation(self):
        with tempfile.TemporaryDirectory(prefix="cm-dream-eval-") as td:
            self.assertFalse(append_dreamer_eval_label(td, candidate_id="", label="true_positive", actionable=True)["ok"])
            out = append_dreamer_eval_label(td, candidate_id="c1", label="maybe", actionable=True)
            self.assertFalse(out["ok"])
            self.assertEqual("invalid_label", out["error"])


if __name__ == "__main__":
    unittest.main()
