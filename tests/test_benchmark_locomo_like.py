import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.locomo_like.runner import run_benchmark
from benchmarks.locomo_like.schema import build_cases, load_fixture_rows, load_gold_rows, validate_fixture_row, validate_gold_row


class TestBenchmarkLocomoLike(unittest.TestCase):
    def test_fixture_and_gold_schema_valid(self):
        base = Path("benchmarks/locomo_like")
        fixtures_dir = base / "fixtures"
        gold_dir = base / "gold"

        fixture_rows = load_fixture_rows(fixtures_dir)
        gold_rows = load_gold_rows(gold_dir)

        self.assertGreaterEqual(len(fixture_rows), 6)
        self.assertGreaterEqual(len(gold_rows), 6)

        for row in fixture_rows:
            ok, errs = validate_fixture_row(row)
            self.assertTrue(ok, f"fixture invalid {row.get('id')}: {errs}")
            self.assertIn(str(row.get("gold_id") or ""), gold_rows)

        for row in gold_rows.values():
            ok, errs = validate_gold_row(row)
            self.assertTrue(ok, f"gold invalid {row.get('id')}: {errs}")

    def test_build_cases_has_all_buckets_covered(self):
        base = Path("benchmarks/locomo_like")
        cases = build_cases(fixtures_dir=base / "fixtures", gold_dir=base / "gold")
        buckets = set()
        for case, _gold in cases:
            buckets.update(case.bucket_labels)

        expected = {
            "current_state_factual",
            "historical_as_of",
            "contradiction_update",
            "causal_mechanism",
            "entity_coreference",
            "preference_identity_policy_commitment_condition",
        }
        self.assertTrue(expected.issubset(buckets))

    def test_runner_smoke_outputs_report_shape(self):
        base = Path("benchmarks/locomo_like")
        report = run_benchmark(
            fixtures_dir=base / "fixtures",
            gold_dir=base / "gold",
            subset="local",
            limit=2,
        )

        self.assertEqual("locomo_like_report.v1", report.get("schema_version"))
        self.assertIn("metadata", report)
        self.assertIn("totals", report)
        self.assertIn("per_bucket", report)
        self.assertIn("cases", report)
        self.assertIn("latency_breakdown_ms", report)
        self.assertIn("queue_observability", report)
        self.assertIn("backend_observability", report)
        self.assertIn("dreamer_correlation", report)
        self.assertIn("myelination_observability", report)
        self.assertIn("token_usage", report)
        self.assertIn("semantic_mode", report.get("metadata") or {})
        self.assertIn("backend_mode", report.get("metadata") or {})
        self.assertIn("benchmark_backend_modes", report.get("metadata") or {})
        self.assertEqual(2, int((report.get("totals") or {}).get("cases") or 0))

        cases = list(report.get("cases") or [])
        self.assertEqual(sorted([c.get("case_id") for c in cases]), [c.get("case_id") for c in cases])
        for c in cases:
            self.assertIn("write_setup_ms", c)
            self.assertIn("retrieval_ms", c)
            self.assertIn("queue_before_query", c)
            self.assertIn("queue_after_query", c)
            self.assertIn("semantic_backend", c)
            self.assertIn("benchmark_backend_mode", c)
            self.assertIn("dreamer_correlation", c)
            self.assertIn("myelination_stats", c)
            self.assertIn("token_usage", c)

        tu = dict(report.get("token_usage") or {})
        self.assertIn("total_tokens_est", tu)
        self.assertGreaterEqual(int(tu.get("cases_with_estimates") or 0), 1)

    def test_runner_supports_required_mode_backend_metadata(self):
        base = Path("benchmarks/locomo_like")
        report = run_benchmark(
            fixtures_dir=base / "fixtures",
            gold_dir=base / "gold",
            subset="local",
            limit=1,
            semantic_mode="required",
            vector_backend="local-faiss",
        )
        modes = set(report.get("metadata", {}).get("benchmark_backend_modes") or [])
        self.assertTrue(modes)
        self.assertIn("strict_missing_backend", modes)

    def test_fixture_validation_allows_turns_only_setup(self):
        row = {
            "id": "fx-turns-only",
            "gold_id": "fx-turns-only",
            "bucket_labels": ["current_state_factual"],
            "query": "what changed",
            "intent": "remember",
            "setup": {
                "turns": [
                    {
                        "session_id": "main",
                        "turn_id": "t1",
                        "user_query": "we changed policy",
                        "assistant_final": "policy changed",
                    }
                ]
            },
        }
        ok, errs = validate_fixture_row(row)
        self.assertTrue(ok, errs)

    def test_runner_preload_turns_file_metadata(self):
        base = Path("benchmarks/locomo_like")
        with tempfile.TemporaryDirectory() as td:
            preload = Path(td) / "preload.jsonl"
            preload.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "session_id": "main",
                                "turn_id": "pl-1",
                                "user_query": "we adopted canary",
                                "assistant_final": "decision logged: canary",
                            }
                        ),
                        json.dumps(
                            {
                                "session_id": "main",
                                "turn_id": "pl-2",
                                "user_query": "timezone is utc",
                                "assistant_final": "timezone confirmed utc",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            report = run_benchmark(
                fixtures_dir=base / "fixtures",
                gold_dir=base / "gold",
                subset="local",
                limit=1,
                preload_turns_file=preload,
            )
            self.assertEqual(2, int((report.get("metadata") or {}).get("preload_turn_count") or 0))
            c0 = (report.get("cases") or [{}])[0]
            self.assertEqual(2, int(c0.get("preload_turn_count") or 0))


if __name__ == "__main__":
    unittest.main()
