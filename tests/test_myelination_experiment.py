import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.locomo_like.runner import run_benchmark
from core_memory.runtime.myelination import compute_myelination_bonus_map
from core_memory.runtime.retrieval_feedback import record_retrieval_feedback


class TestMyelinationExperiment(unittest.TestCase):
    def test_bonus_map_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            out = compute_myelination_bonus_map(td)
            self.assertFalse(out.get("enabled"))
            self.assertEqual({}, out.get("bonus_by_bead_id") or {})

    def test_bonus_map_strengthens_and_weakens_from_feedback(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_MYELINATION_ENABLED": "1",
                "CORE_MEMORY_MYELINATION_MIN_HITS": "1",
            },
            clear=False,
        ):
            a = "bead-a"
            b = "bead-b"
            c = "bead-c"

            # Edge (a->b supports) gets successful retrieval support.
            record_retrieval_feedback(
                td,
                request={"raw_query": "q1", "intent": "remember", "k": 5},
                response={
                    "ok": True,
                    "answer_outcome": "answer_current",
                    "results": [
                        {"bead_id": a, "score": 0.9, "source_surface": "session_bead"},
                        {"bead_id": b, "score": 0.8, "source_surface": "session_bead"},
                    ],
                    "chains": [{"edges": [{"src": a, "dst": b, "rel": "supports"}]}],
                },
                source="unit_test",
            )

            # Edge (b->c contradicts) gets failed retrieval exposure.
            record_retrieval_feedback(
                td,
                request={"raw_query": "q2", "intent": "remember", "k": 5},
                response={
                    "ok": False,
                    "answer_outcome": "abstain",
                    "results": [
                        {"bead_id": b, "score": 0.4, "source_surface": "session_bead"},
                        {"bead_id": c, "score": 0.3, "source_surface": "session_bead"},
                    ],
                    "chains": [{"edges": [{"src": b, "dst": c, "rel": "contradicts"}]}],
                },
                source="unit_test",
            )

            out = compute_myelination_bonus_map(td)
            self.assertTrue(out.get("enabled"))
            edge_bonus = dict(out.get("bonus_by_edge_key") or {})
            self.assertTrue(edge_bonus)
            bonus = dict(out.get("bonus_by_bead_id") or {})
            self.assertGreater(float(bonus.get(a) or 0.0), 0.0)
            self.assertLess(float(bonus.get(c) or 0.0), 0.0)

    def test_benchmark_compare_mode_outputs_comparison_section(self):
        base = Path("benchmarks/locomo_like")
        report = run_benchmark(
            fixtures_dir=base / "fixtures",
            gold_dir=base / "gold",
            subset="local",
            limit=1,
            myelination_mode="compare",
        )
        self.assertEqual("compare", str((report.get("metadata") or {}).get("myelination_mode") or ""))
        comp = dict(report.get("myelination_comparison") or {})
        self.assertIn("baseline", comp)
        self.assertIn("enabled", comp)
        self.assertIn("accuracy_delta", comp)
        mo = dict(report.get("myelination_observability") or {})
        self.assertIn("strengthened_total", mo)
        self.assertIn("weakened_total", mo)


if __name__ == "__main__":
    unittest.main()
