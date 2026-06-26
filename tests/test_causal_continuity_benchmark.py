"""Tests for the causal-continuity suite harness."""
from __future__ import annotations

import unittest
from pathlib import Path

from benchmarks.causal_continuity.reporting import render_summary
from benchmarks.causal_continuity.runner import _parse_strategies, _parse_tasks, run_suite
from benchmarks.causal_continuity.t1 import available_strategies, run_t1_matrix
from benchmarks.causal_continuity.t2 import run_t2_calibration
from benchmarks.causal_continuity.t3 import run_t3_temporal_state
from benchmarks.causal_continuity.t4 import run_t4_longitudinal_continuity
from benchmarks.causal_continuity.t5 import run_t5_thread_fidelity

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

    def test_parse_tasks(self):
        self.assertEqual(["t1", "t2", "t3", "t4", "t5"], _parse_tasks("all"))
        self.assertEqual(["t2"], _parse_tasks("t2"))
        with self.assertRaises(ValueError):
            _parse_tasks("t6")

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
            tasks=["t1"],
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

    def test_t2_calibration_scores_meter_output(self):
        report = run_t2_calibration()

        self.assertEqual("causal_continuity.t2_calibration.v1", report["schema_version"])
        self.assertEqual("t2_calibration_reliability", report["task_id"])
        self.assertTrue(report["metadata"]["faithfulness"]["is_faithful"])
        self.assertTrue(report["pass"], report)

        metrics = report["metrics"]
        self.assertGreaterEqual(float(metrics["spearman_rho"]), 0.7)
        self.assertLessEqual(float(metrics["expected_calibration_error"]), 0.2)
        self.assertLessEqual(float(metrics["brier_score"]), 0.2)
        self.assertGreaterEqual(float(metrics["high_band_usefulness_rate"]), 0.8)
        self.assertEqual(20, metrics["sample_count"])

    def test_suite_report_includes_t2_headlines(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["bm25"],
            tasks=["t2"],
            subset="local",
            limit=1,
        )

        self.assertEqual("causal_continuity_report.v1", report["schema_version"])
        self.assertIn("t2_calibration_reliability", report["tasks"])
        self.assertNotIn("t1_causal_chain_reconstruction", report["tasks"])
        self.assertIn("t2:calibration_reliability", report["faithfulness"]["by_scope"])
        self.assertTrue(report["headlines"]["t2_calibration_reliability"]["pass"])

        text = render_summary(report)
        self.assertIn("T2 calibration reliability", text)
        self.assertIn("brier=", text)

    def test_t3_temporal_state_scores_as_of_supersession_and_conflict(self):
        report = run_t3_temporal_state()

        self.assertEqual("causal_continuity.t3_temporal_state.v1", report["schema_version"])
        self.assertEqual("t3_temporal_state_selection", report["task_id"])
        self.assertTrue(report["metadata"]["faithfulness"]["is_faithful"])
        self.assertTrue(report["pass"], report)

        metrics = report["metrics"]
        self.assertEqual(4, metrics["case_count"])
        self.assertEqual(2, metrics["as_of_case_count"])
        self.assertEqual(1.0, metrics["correct_state_selection_rate"])
        self.assertEqual(1.0, metrics["as_of_accuracy"])
        self.assertEqual(1.0, metrics["supersession_respect_rate"])
        self.assertEqual(1.0, metrics["contradiction_surfaced_rate"])

        conflict = next(c for c in report["cases"] if c["case_id"] == "t3_conflicting_coding_preference_surfaced")
        self.assertEqual("conflict", conflict["actual"]["status"])
        self.assertIn("conflict_surfaced", conflict["checks"])

    def test_suite_report_includes_t3_headlines(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["bm25"],
            tasks=["t3"],
            subset="local",
            limit=1,
        )

        self.assertEqual("causal_continuity_report.v1", report["schema_version"])
        self.assertIn("t3_temporal_state_selection", report["tasks"])
        self.assertNotIn("t1_causal_chain_reconstruction", report["tasks"])
        self.assertIn("t3:temporal_state_selection", report["faithfulness"]["by_scope"])
        self.assertTrue(report["headlines"]["t3_temporal_state_selection"]["pass"])

        text = render_summary(report)
        self.assertIn("T3 temporal state selection", text)
        self.assertIn("supersession=", text)

    def test_t4_longitudinal_continuity_scores_lift_drift_and_goal_persistence(self):
        report = run_t4_longitudinal_continuity()

        self.assertEqual("causal_continuity.t4_longitudinal_continuity.v1", report["schema_version"])
        self.assertEqual("t4_longitudinal_continuity", report["task_id"])
        self.assertTrue(report["metadata"]["faithfulness"]["is_faithful"])
        self.assertTrue(report["pass"], report)

        metrics = report["metrics"]
        self.assertGreater(float(metrics["continuity_lift"]), 0.0)
        self.assertEqual(0, metrics["self_model_drift_score"])
        self.assertEqual("healthy", metrics["self_model_drift_status"])
        self.assertEqual(1.0, metrics["goal_thread_persistence_rate"])
        self.assertGreaterEqual(metrics["accepted_applied_structural_candidates"], 1)

    def test_suite_report_includes_t4_headlines(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["bm25"],
            tasks=["t4"],
            subset="local",
            limit=1,
        )

        self.assertEqual("causal_continuity_report.v1", report["schema_version"])
        self.assertIn("t4_longitudinal_continuity", report["tasks"])
        self.assertNotIn("t1_causal_chain_reconstruction", report["tasks"])
        self.assertIn("t4:longitudinal_continuity", report["faithfulness"]["by_scope"])
        self.assertTrue(report["headlines"]["t4_longitudinal_continuity"]["pass"])

        text = render_summary(report)
        self.assertIn("T4 longitudinal continuity", text)
        self.assertIn("goal_persistence=", text)

    def test_t5_thread_fidelity_scores_storyline_thread_and_query_drift(self):
        report = run_t5_thread_fidelity()

        self.assertEqual("causal_continuity.t5_thread_fidelity.v1", report["schema_version"])
        self.assertEqual("t5_thread_fidelity", report["task_id"])
        self.assertTrue(report["metadata"]["faithfulness"]["is_faithful"])
        self.assertTrue(report["pass"], report)

        metrics = report["metrics"]
        self.assertEqual(1, metrics["case_count"])
        self.assertGreaterEqual(metrics["thread_precision"], 0.75)
        self.assertEqual(1.0, metrics["thread_recall"])
        self.assertGreaterEqual(metrics["thread_f1"], 0.85)
        self.assertEqual(1.0, metrics["answerability"])
        self.assertLessEqual(metrics["query_drift_rate"], 0.25)

        case = report["cases"][0]
        self.assertTrue(case["loop"]["steps"])
        self.assertEqual(set(case["gold_thread_bead_ids"]), set(case["returned_thread_bead_ids"]))

    def test_suite_report_includes_t5_headlines(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["bm25"],
            tasks=["t5"],
            subset="local",
            limit=1,
        )

        self.assertEqual("causal_continuity_report.v1", report["schema_version"])
        self.assertIn("t5_thread_fidelity", report["tasks"])
        self.assertNotIn("t1_causal_chain_reconstruction", report["tasks"])
        self.assertIn("t5:thread_fidelity", report["faithfulness"]["by_scope"])
        self.assertTrue(report["headlines"]["t5_thread_fidelity"]["pass"])

        text = render_summary(report)
        self.assertIn("T5 thread fidelity", text)
        self.assertIn("answerability=", text)


if __name__ == "__main__":
    unittest.main()
