import unittest
import tempfile
import json
from pathlib import Path

from benchmarks.locomo_like.reporting import build_report
from benchmarks.locomo_like.runner import run_benchmark


class TestBenchmarkDreamerCorrelation(unittest.TestCase):
    def test_report_aggregates_dreamer_use_metrics(self):
        report = build_report(
            metadata={"runner": "locomo_like", "semantic_mode": "degraded_allowed", "backend_mode": "local-faiss"},
            case_results=[
                {
                    "case_id": "a",
                    "bucket_labels": ["current_state_factual"],
                    "pass": True,
                    "latency_ms": 12.0,
                    "write_setup_ms": 4.0,
                    "retrieval_ms": 8.0,
                    "warnings": [],
                    "queue_before_query": {"pending_total": 0},
                    "queue_after_query": {"pending_total": 0},
                    "semantic_backend": {"ok": True},
                    "benchmark_backend_mode": "degraded_lexical",
                    "dreamer_correlation": {
                        "accepted_total": 2,
                        "accepted_applied_total": 1,
                        "accepted_used_total": 1,
                        "accepted_applied_used_total": 1,
                    },
                },
                {
                    "case_id": "b",
                    "bucket_labels": ["causal_mechanism"],
                    "pass": False,
                    "latency_ms": 15.0,
                    "write_setup_ms": 5.0,
                    "retrieval_ms": 10.0,
                    "warnings": [],
                    "queue_before_query": {"pending_total": 1},
                    "queue_after_query": {"pending_total": 0},
                    "semantic_backend": {"ok": True},
                    "benchmark_backend_mode": "degraded_lexical",
                    "dreamer_correlation": {
                        "accepted_total": 1,
                        "accepted_applied_total": 1,
                        "accepted_used_total": 0,
                        "accepted_applied_used_total": 0,
                    },
                },
            ],
        )

        dc = dict(report.get("dreamer_correlation") or {})
        self.assertEqual(3, int(dc.get("accepted_candidates_total") or 0))
        self.assertEqual(2, int(dc.get("accepted_applied_total") or 0))
        self.assertEqual(1, int(dc.get("accepted_used_in_retrieval_total") or 0))
        self.assertAlmostEqual(0.3333, float(dc.get("retrieval_use_rate") or 0.0), places=4)
        self.assertIsNotNone(dc.get("accuracy_when_used"))
        self.assertIsNotNone(dc.get("accuracy_when_not_used"))

    def test_runner_reports_nonzero_dreamer_use_when_fixture_auto_accepts(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            fixtures = base / "fixtures"
            gold = base / "gold"
            fixtures.mkdir(parents=True, exist_ok=True)
            gold.mkdir(parents=True, exist_ok=True)

            fx_row = {
                "id": "fx_dreamer_use",
                "gold_id": "fx_dreamer_use",
                "bucket_labels": ["causal_mechanism"],
                "query": "why did deployment fail",
                "intent": "causal",
                "constraints": {"require_structural": False},
                "k": 5,
                "setup": {
                    "beads": [
                        {
                            "key": "a",
                            "type": "decision",
                            "title": "Deploy single replica",
                            "summary": ["single replica rollout"],
                            "session_id": "main",
                            "source_turn_ids": ["fxd1"],
                        },
                        {
                            "key": "b",
                            "type": "evidence",
                            "title": "OOM logs",
                            "summary": ["memory pressure logs"],
                            "session_id": "main",
                            "source_turn_ids": ["fxd1"],
                        },
                    ],
                    "dreamer_associations": [
                        {
                            "source_key": "a",
                            "target_key": "b",
                            "relationship": "supports",
                            "novelty": 0.8,
                            "grounding": 0.9,
                            "confidence": 0.9,
                        }
                    ],
                    "dreamer_auto_accept": ["retrieval_value_candidate"],
                },
            }
            (fixtures / "fx.jsonl").write_text(json.dumps(fx_row) + "\n", encoding="utf-8")

            gold_payload = {
                "cases": [
                    {
                        "id": "fx_dreamer_use",
                        "expected_answer_class": "answer_partial",
                        "bucket_labels": ["causal_mechanism"],
                    }
                ]
            }
            (gold / "gold.json").write_text(json.dumps(gold_payload, indent=2), encoding="utf-8")

            report = run_benchmark(fixtures_dir=fixtures, gold_dir=gold, subset="full", limit=1)
            dc = dict(report.get("dreamer_correlation") or {})
            self.assertIn("accepted_candidates_total", dc)
            self.assertIn("accepted_applied_total", dc)
            self.assertIn("accepted_used_in_retrieval_total", dc)
            case_dc = dict((report.get("cases") or [{}])[0].get("dreamer_correlation") or {})
            self.assertIn("accepted_total", case_dc)
            self.assertIn("accepted_applied_total", case_dc)


if __name__ == "__main__":
    unittest.main()
