"""Tests for the causal-continuity suite harness."""
from __future__ import annotations

import unittest
from pathlib import Path

from benchmarks.causal_continuity.reporting import render_summary
from benchmarks.causal_continuity.runner import _parse_strategies, run_suite
from benchmarks.causal_continuity.t1 import available_strategies, run_t1_matrix

_HERE = Path(__file__).resolve().parent.parent / "benchmarks" / "causal"
_FIXTURES = _HERE / "fixtures"
_GOLD = _HERE / "gold"


class TestCausalContinuityT1(unittest.TestCase):
    def test_available_strategies_include_pr1_matrix(self):
        self.assertEqual(
            ("core_memory_full", "bm25", "similarity_only"),
            available_strategies(),
        )

    def test_parse_strategies(self):
        self.assertEqual(["bm25", "similarity_only"], _parse_strategies("bm25,similarity_only"))
        self.assertIn("core_memory_full", _parse_strategies("all"))
        with self.assertRaises(ValueError):
            _parse_strategies("unknown")

    def test_t1_baseline_matrix_reports_faithful_strategy_rows(self):
        report = run_t1_matrix(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["bm25", "similarity_only"],
            subset="local",
            limit=1,
        )

        self.assertEqual("causal_continuity.t1_matrix.v1", report["schema_version"])
        self.assertEqual("t1_causal_chain_reconstruction", report["task_id"])
        self.assertEqual(["bm25", "similarity_only"], report["strategies"])
        self.assertEqual({"bm25", "similarity_only"}, set(report["strategy_matrix"]))
        self.assertEqual(1, report["case_count"])
        self.assertEqual(1, len(report["case_matrix"]))

        for strategy in ("bm25", "similarity_only"):
            row = report["strategy_matrix"][strategy]
            self.assertEqual(1, row["cases"])
            self.assertIn("causal_survival_rate", row)
            self.assertIn("edge_f1_mean", row)

            meta = report["strategy_reports"][strategy]["metadata"]
            self.assertEqual("causal_continuity.t1", meta["runner"])
            self.assertTrue(meta["faithfulness"]["is_faithful"])
            self.assertTrue(meta["shortcut_flags"]["is_faithful"])

    def test_suite_report_wraps_t1_with_headlines_and_faithfulness(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["bm25"],
            subset="local",
            limit=1,
        )

        self.assertEqual("causal_continuity_report.v1", report["schema_version"])
        self.assertTrue(report["faithfulness"]["is_faithful"])
        self.assertIn("bm25", report["faithfulness"]["by_strategy"])
        self.assertIn("t1_causal_chain_reconstruction", report["tasks"])
        self.assertIn(
            "causal_survival_rate_by_strategy",
            report["headlines"]["t1_causal_chain_reconstruction"],
        )

        text = render_summary(report)
        self.assertIn("Causal-Continuity Evaluation Suite", text)
        self.assertIn("bm25", text)
        self.assertIn("CSR=", text)


if __name__ == "__main__":
    unittest.main()
