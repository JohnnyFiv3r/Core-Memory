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
        self.assertEqual(2, int((report.get("totals") or {}).get("cases") or 0))

        cases = list(report.get("cases") or [])
        self.assertEqual(sorted([c.get("case_id") for c in cases]), [c.get("case_id") for c in cases])


if __name__ == "__main__":
    unittest.main()
