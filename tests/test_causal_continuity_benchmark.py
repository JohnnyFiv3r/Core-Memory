"""Tests for the causal-continuity suite harness."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from benchmarks.contracts import BenchmarkAdapter
from benchmarks.causal_continuity.ablations import build_ablation_matrix
from benchmarks.causal_continuity.real_data import build_real_data_contrast
from benchmarks.causal_continuity.reporting import render_summary
from benchmarks.causal_continuity.reproducibility import run_reproducibility_check
from benchmarks.causal_continuity.runtime_ablations import run_runtime_ablation_toggles
from benchmarks.causal_continuity.runner import _parse_strategies, _parse_tasks, run_suite
from benchmarks.longmemeval.loader import LongMemEvalAdapter, load_longmemeval_corpus
from benchmarks.longmemeval.runner import run_adapter_smoke, run_evaluation_smoke
from benchmarks.causal_continuity.t1 import available_strategies, run_t1_matrix
from benchmarks.causal_continuity.t2 import run_t2_calibration
from benchmarks.causal_continuity.t3 import run_t3_temporal_state
from benchmarks.causal_continuity.t4 import run_t4_longitudinal_continuity
from benchmarks.causal_continuity.t5 import run_t5_thread_fidelity

_HERE = Path(__file__).resolve().parent.parent / "benchmarks" / "causal"
_REPO = _HERE.parent.parent
_FIXTURES = _HERE / "fixtures"
_GOLD = _HERE / "gold"


def _write_t1_command_adapter(path: Path) -> Path:
    script = r'''
import json
import sys

request = json.load(sys.stdin)
record_path = sys.argv[1]
with open(record_path, "w", encoding="utf-8") as f:
    json.dump(request, f, sort_keys=True)

documents = request.get("documents") or []
ranked = []
for index, doc in enumerate(documents, start=1):
    ranked.append({
        "key": doc.get("key"),
        "score": 1.0 / float(index),
        "reason": "test command ranking",
    })

json.dump({
    "schema_version": "causal_continuity.t1_adapter_response.v1",
    "status": "completed",
    "adapter_name": "test_command_adapter",
    "ranked_keys": ranked,
}, sys.stdout)
'''
    path.write_text(script, encoding="utf-8")
    return path


def _write_longmemeval_fixture(path: Path) -> Path:
    payload = [
        {
            "question_id": "lme_test_001",
            "question_type": "single-session-user",
            "question": "Which project did Mira mention?",
            "answer": "Northstar",
            "question_date": "2025-02-03",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2025-02-01", "2025-02-02"],
            "answer_session_ids": ["s2"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "Mira talked about garden plans."},
                    {"role": "assistant", "content": "I noted the garden plan."},
                ],
                [
                    {"role": "user", "content": "Mira said the project codename is Northstar.", "has_answer": True},
                    {"role": "assistant", "content": "Northstar is now the project codename."},
                ],
            ],
        }
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestCausalContinuityT1(unittest.TestCase):
    def test_available_strategies_include_pr1_matrix(self):
        self.assertEqual(
            (
                "core_memory_full",
                "bm25",
                "similarity_only",
                "dense_vector",
                "long_context_no_memory",
                "external_memory_adapter",
            ),
            available_strategies(),
        )

    def test_parse_strategies(self):
        self.assertEqual(["bm25", "similarity_only"], _parse_strategies("bm25,similarity_only"))
        self.assertIn("core_memory_full", _parse_strategies("all"))
        self.assertIn("dense_vector", _parse_strategies("all"))
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
            self.assertEqual("completed", row["status"])
            self.assertEqual(1, row["cases"])
            self.assertIn("causal_survival_rate", row)
            self.assertIn("edge_f1_mean", row)
            self.assertFalse(row["uses_causal_traversal"])

            meta = report["strategy_reports"][strategy]["metadata"]
            self.assertEqual("causal_continuity.t1", meta["runner"])
            self.assertTrue(meta["faithfulness"]["is_faithful"])
            self.assertTrue(meta["shortcut_flags"]["is_faithful"])

    def test_t1_matrix_executes_long_context_proxy_and_keeps_external_adapter_honest(self):
        report = run_t1_matrix(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=available_strategies(),
            subset="local",
            limit=1,
        )

        matrix = report["strategy_matrix"]
        self.assertEqual(set(available_strategies()), set(matrix))

        self.assertEqual("proxy_executed", matrix["dense_vector"]["status"])
        self.assertEqual("dense_vector_proxy", matrix["dense_vector"]["baseline_kind"])
        self.assertFalse(matrix["dense_vector"]["uses_causal_traversal"])
        self.assertFalse(matrix["dense_vector"]["leaderboard_claim"])

        long_context = matrix["long_context_no_memory"]
        self.assertEqual("proxy_executed", long_context["status"])
        self.assertEqual("long_context_local_proxy", long_context["baseline_kind"])
        self.assertEqual("local_proxy", long_context["execution_mode"])
        self.assertEqual("proxy_executed", long_context["adapter_status"])
        self.assertEqual(1, long_context["cases"])
        self.assertFalse(long_context["uses_causal_traversal"])
        self.assertFalse(long_context["leaderboard_claim"])

        external = matrix["external_memory_adapter"]
        self.assertEqual("unavailable", external["status"])
        self.assertEqual(0, external["cases"])
        self.assertFalse(external["uses_causal_traversal"])
        self.assertFalse(external["leaderboard_claim"])
        self.assertTrue(external["unavailable_reason"])
        meta = report["strategy_reports"]["external_memory_adapter"]["metadata"]
        self.assertTrue(meta["faithfulness"]["is_faithful"])
        self.assertTrue(meta["shortcut_flags"]["is_faithful"])

    def test_t1_external_memory_fake_adapter_executes_contract(self):
        report = run_t1_matrix(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["external_memory_adapter"],
            subset="local",
            limit=1,
            external_memory_adapter="fake",
        )

        row = report["strategy_matrix"]["external_memory_adapter"]
        self.assertEqual("adapter_executed", row["status"])
        self.assertEqual("adapter_fake", row["execution_mode"])
        self.assertEqual("completed", row["adapter_status"])
        self.assertEqual("fake_external_memory_adapter", row["adapter_name"])
        self.assertEqual(1, row["cases"])
        self.assertFalse(row["uses_causal_traversal"])
        self.assertFalse(row["leaderboard_claim"])

    def test_t1_external_memory_command_adapter_executes_without_answer_key(self):
        with tempfile.TemporaryDirectory() as td:
            script = _write_t1_command_adapter(Path(td) / "adapter.py")
            record = Path(td) / "request.json"

            report = run_t1_matrix(
                fixtures_dir=_FIXTURES,
                gold_dir=_GOLD,
                strategies=["external_memory_adapter"],
                subset="local",
                limit=1,
                external_memory_adapter="command",
                external_memory_command=[sys.executable, str(script), str(record)],
            )

            row = report["strategy_matrix"]["external_memory_adapter"]
            self.assertEqual("adapter_executed", row["status"])
            self.assertEqual("adapter_command", row["execution_mode"])
            self.assertEqual("completed", row["adapter_status"])
            self.assertEqual("test_command_adapter", row["adapter_name"])
            self.assertEqual(1, row["cases"])
            self.assertFalse(row["uses_causal_traversal"])
            self.assertFalse(row["leaderboard_claim"])

            request = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual("causal_continuity.t1_adapter_request.v1", request["schema_version"])
            self.assertNotIn("edges", request)
            self.assertNotIn("gold", request)
            self.assertFalse(request["constraints"]["includes_causal_edges"])
            self.assertFalse(request["constraints"]["includes_gold_labels"])
            self.assertTrue(request["documents"])
            self.assertNotIn("bead_id", request["documents"][0])

    def test_t1_long_context_command_adapter_overrides_local_proxy(self):
        with tempfile.TemporaryDirectory() as td:
            script = _write_t1_command_adapter(Path(td) / "adapter.py")
            record = Path(td) / "request.json"

            report = run_t1_matrix(
                fixtures_dir=_FIXTURES,
                gold_dir=_GOLD,
                strategies=["long_context_no_memory"],
                subset="local",
                limit=1,
                long_context_adapter="command",
                long_context_command=[sys.executable, str(script), str(record)],
            )

            row = report["strategy_matrix"]["long_context_no_memory"]
            self.assertEqual("adapter_executed", row["status"])
            self.assertEqual("long_context_provider_adapter", row["baseline_kind"])
            self.assertEqual("configured_command_adapter", row["availability"])
            self.assertEqual("adapter_command", row["execution_mode"])
            self.assertEqual("test_command_adapter", row["adapter_name"])
            self.assertFalse(row["uses_causal_traversal"])
            self.assertFalse(row["leaderboard_claim"])

            request = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual("long_context_no_memory", request["strategy"])
            self.assertFalse(request["constraints"]["uses_causal_traversal"])
            self.assertFalse(request["constraints"]["leaderboard_claim"])

    def test_t1_command_adapter_missing_command_reports_unavailable(self):
        report = run_t1_matrix(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["long_context_no_memory"],
            subset="local",
            limit=1,
            long_context_adapter="command",
        )

        row = report["strategy_matrix"]["long_context_no_memory"]
        self.assertEqual("unavailable", row["status"])
        self.assertEqual("long_context_command_not_configured", row["unavailable_reason"])
        self.assertEqual("unavailable", row["adapter_status"])
        self.assertEqual(0, row["cases"])
        self.assertFalse(row["leaderboard_claim"])

    def test_t1_command_adapter_failure_reports_failed_row(self):
        report = run_t1_matrix(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["external_memory_adapter"],
            subset="local",
            limit=1,
            external_memory_adapter="command",
            external_memory_command=[sys.executable, "-c", "import sys; sys.exit(3)"],
        )

        row = report["strategy_matrix"]["external_memory_adapter"]
        self.assertEqual("failed", row["status"])
        self.assertEqual("adapter_command", row["execution_mode"])
        self.assertEqual("failed", row["adapter_status"])
        self.assertEqual("configured_adapter_failed", row["availability"])
        self.assertIn("adapter_command_failed:3", row["failure_reason"])
        self.assertEqual(0, row["cases"])
        self.assertFalse(row["leaderboard_claim"])

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
        self.assertIn(
            "status_by_strategy",
            report["headlines"]["t1_causal_chain_reconstruction"],
        )

        text = render_summary(report)
        self.assertIn("Causal-Continuity Evaluation Suite", text)
        self.assertIn("bm25", text)
        self.assertIn("CSR=", text)
        self.assertNotIn("status=unavailable", text)

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

    def test_t2_calibration_disabled_outcome_feedback_has_no_samples(self):
        report = run_t2_calibration(record_validated_outcomes=False)

        self.assertEqual(0, report["metrics"]["sample_count"])
        self.assertFalse(report["pass"])
        self.assertFalse(report["metadata"]["ablation_mode"]["record_validated_outcomes"])

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

    def test_t3_disabled_claim_updates_drops_supersession_and_conflict(self):
        report = run_t3_temporal_state(apply_claim_updates=False)

        self.assertFalse(report["pass"])
        self.assertFalse(report["metadata"]["ablation_mode"]["apply_claim_updates"])
        self.assertLess(float(report["metrics"]["supersession_respect_rate"] or 0.0), 1.0)
        self.assertLess(float(report["metrics"]["contradiction_surfaced_rate"] or 0.0), 1.0)

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
        self.assertIn("one_shot_thread_f1", metrics)
        self.assertIn("agentic_loop_thread_f1_lift", metrics)
        self.assertGreater(metrics["agentic_loop_thread_f1_lift"], 0.0)

        case = report["cases"][0]
        self.assertTrue(case["loop"]["steps"])
        self.assertEqual(set(case["gold_thread_bead_ids"]), set(case["returned_thread_bead_ids"]))
        self.assertEqual(set(case["gold_thread_keys"]), set(case["returned_thread_keys"]))
        self.assertEqual("deterministic", case["judge_kind"])
        self.assertEqual("completed", case["judge_status"])
        self.assertFalse(case["is_llm_judge"])

    def test_t5_fake_llm_judge_records_supplemental_path(self):
        report = run_t5_thread_fidelity(judge_kind="fake_llm")

        self.assertTrue(report["pass"], report)
        self.assertTrue(report["metadata"]["judge"]["is_llm_judge"])
        case = report["cases"][0]
        self.assertEqual("fake_llm", case["judge_kind"])
        self.assertEqual("completed", case["judge_status"])
        self.assertTrue(case["is_llm_judge"])
        self.assertEqual(1.0, case["answerability_judge"]["answerability"])

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

    def test_ablation_matrix_attaches_mechanism_rows_and_gaps(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=available_strategies(),
            tasks=["t1", "t2", "t3", "t4", "t5"],
            subset="local",
            limit=1,
            include_ablations=True,
        )

        matrix = report["ablation_matrix"]
        self.assertEqual("causal_continuity.ablation_matrix.v1", matrix["schema_version"])
        self.assertTrue(matrix["coverage"]["faithfulness_clean"])
        self.assertGreaterEqual(matrix["coverage"]["observed_rows"], 3)
        self.assertGreaterEqual(matrix["coverage"]["needs_runtime_toggle_rows"], 1)

        rows = {row["id"]: row for row in matrix["rows"]}
        self.assertIn("core_memory_full", rows)
        self.assertIn("minus_causal_traversal", rows)
        self.assertIn("minus_dreamer", rows)
        self.assertIn("minus_agentic_recall_loop", rows)
        self.assertIn(rows["minus_agentic_recall_loop"]["status"], {"observed", "observed_no_expected_drop"})
        self.assertEqual("needs_runtime_toggle", rows["minus_myelination_backpressure"]["status"])

        text = render_summary(report)
        self.assertIn("Ablation matrix", text)
        self.assertIn("needs_toggle=", text)

    def test_ablation_matrix_can_be_built_from_report(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=available_strategies(),
            tasks=["t4", "t5"],
            subset="local",
            limit=1,
        )

        matrix = build_ablation_matrix(report)
        rows = {row["id"]: row for row in matrix["rows"]}
        self.assertIn(rows["minus_agentic_recall_loop"]["status"], {"observed", "observed_no_expected_drop"})
        self.assertIn("t5_thread_f1", rows["minus_agentic_recall_loop"]["scores"])

    def test_runtime_ablation_toggles_overlay_disabled_mode_runs(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=available_strategies(),
            tasks=["t1", "t2", "t3", "t4", "t5"],
            subset="local",
            limit=1,
            run_ablation_toggles=True,
        )

        matrix = report["ablation_matrix"]
        self.assertEqual("runtime_ablation_matrix", matrix["methodology"]["kind"])
        self.assertEqual(0, matrix["coverage"]["needs_runtime_toggle_rows"])
        self.assertEqual("causal_continuity.runtime_ablation_runs.v1", matrix["runtime_ablation_runs"]["schema_version"])

        rows = {row["id"]: row for row in matrix["rows"]}
        self.assertEqual("observed", rows["minus_myelination_backpressure"]["status"])
        self.assertEqual("observed", rows["minus_validated_outcome_reward"]["status"])
        self.assertEqual(0.0, rows["minus_validated_outcome_reward"]["scores"]["t2_sample_count"])
        self.assertEqual("observed", rows["minus_supersession_temporal_filter"]["status"])
        self.assertEqual("observed", rows["minus_agentic_recall_loop"]["status"])
        self.assertEqual("observed", rows["minus_causal_traversal"]["status"])
        self.assertIn("runtime_run", rows["minus_supersession_temporal_filter"])

    def test_evidence_manifest_gates_local_and_external_claims(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=available_strategies(),
            tasks=["t1", "t2", "t3", "t4", "t5"],
            subset="local",
            limit=1,
            run_ablation_toggles=True,
            include_real_data_contrast=True,
        )

        manifest = report["evidence_manifest"]
        self.assertEqual("causal_continuity.evidence_manifest.v1", manifest["schema_version"])
        self.assertTrue(manifest["claim_gates"]["local_fixture_claim_ready"])
        self.assertFalse(manifest["claim_gates"]["provider_backed_comparison_ready"])
        self.assertFalse(manifest["claim_gates"]["real_data_leaderboard_ready"])
        self.assertFalse(manifest["claim_gates"]["t5_llm_judge_primary_claim_ready"])

        tiers = manifest["tiers"]
        self.assertEqual("ready", tiers["local_deterministic"]["status"])
        self.assertEqual([], tiers["local_deterministic"]["blockers"])
        self.assertEqual("proxy_only", tiers["proxy_comparator"]["status"])
        self.assertIn("long_context_no_memory", tiers["proxy_comparator"]["rows"])
        self.assertEqual("unavailable", tiers["configured_adapter"]["status"])
        self.assertIn("external_memory_adapter", tiers["configured_adapter"]["unavailable_rows"])
        self.assertIn("no_provider_command_adapter_run", tiers["configured_adapter"]["blockers"])
        self.assertEqual("dataset_required", tiers["real_data_external"]["status"])
        self.assertEqual("deterministic_default", tiers["t5_judge"]["status"])

    def test_evidence_manifest_records_configured_command_adapter_execution(self):
        with tempfile.TemporaryDirectory() as td:
            script = _write_t1_command_adapter(Path(td) / "adapter.py")
            record = Path(td) / "request.json"

            report = run_suite(
                fixtures_dir=_FIXTURES,
                gold_dir=_GOLD,
                strategies=["external_memory_adapter"],
                tasks=["t1"],
                subset="local",
                limit=1,
                external_memory_adapter="command",
                external_memory_command=[sys.executable, str(script), str(record)],
            )

        manifest = report["evidence_manifest"]
        adapter = manifest["tiers"]["configured_adapter"]
        self.assertEqual("configured_adapter_executed", adapter["status"])
        self.assertEqual(["external_memory_adapter"], adapter["command_adapter_rows"])
        self.assertFalse(manifest["claim_gates"]["provider_backed_comparison_ready"])
        self.assertIn("no_provider_leaderboard_claim_rows", adapter["blockers"])

    def test_runtime_ablation_helper_can_overlay_existing_report(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=available_strategies(),
            tasks=["t1", "t2", "t3", "t4", "t5"],
            subset="local",
            limit=1,
        )

        runtime_runs = run_runtime_ablation_toggles(report)
        matrix = build_ablation_matrix(report, runtime_runs=runtime_runs)
        self.assertEqual(0, matrix["coverage"]["needs_runtime_toggle_rows"])
        self.assertEqual(len(matrix["rows"]) - 1, runtime_runs["summary"]["row_count"])

    def test_reproducibility_check_records_stable_ordered_outputs(self):
        report = run_reproducibility_check(repeats=2)

        self.assertEqual("causal_continuity.reproducibility.v1", report["schema_version"])
        self.assertTrue(report["determinism"]["stable_headlines"], report)
        self.assertTrue(report["determinism"]["stable_ordered_topk"], report)
        self.assertTrue(report["determinism"]["passed"], report)
        self.assertEqual("stable", report["determinism"]["status"])
        self.assertEqual(2, report["determinism"]["run_count"])
        self.assertTrue(report["runs"][0]["ordered_topk"])

    def test_committed_reproducibility_artifacts_are_internally_consistent(self):
        local = json.loads((_REPO / "benchmarks/reports/causal-continuity-local-report.json").read_text(encoding="utf-8"))
        repro = json.loads((_REPO / "benchmarks/reports/causal-continuity-reproducibility.json").read_text(encoding="utf-8"))
        appendix = (_REPO / "docs/eval/causal-continuity-reproducibility-appendix.md").read_text(encoding="utf-8")

        source_commit = str(local["metadata"]["commit"])
        self.assertEqual(source_commit, repro["source_commit"])
        self.assertIn(f"source commit `{source_commit}`", appendix)
        self.assertIn(f"Source commit used to generate the artifacts: `{source_commit}`", appendix)
        self.assertTrue(repro["determinism"]["stable_headlines"])
        self.assertTrue(repro["determinism"]["stable_ordered_topk"])

        matrix = local["tasks"]["t1_causal_chain_reconstruction"]["strategy_matrix"]
        self.assertTrue(matrix)
        for row in matrix.values():
            self.assertIn("failure_reason", row)

    def test_real_data_contrast_declares_local_proxy_without_leaderboard_claim(self):
        report = build_real_data_contrast()

        self.assertEqual("causal_continuity.real_data_contrast.v1", report["schema_version"])
        self.assertEqual(0, report["summary"]["leaderboard_claim_count"])
        self.assertEqual(
            ["load_conversations", "score_answer", "score_evidence"],
            report["adapter_contract"]["required_methods"],
        )

        rows = {row["dataset_id"]: row for row in report["datasets"]}
        self.assertIn("locomo_like_local_proxy", rows)
        self.assertIn("locomo_external", rows)
        self.assertIn("longmemeval_external", rows)
        self.assertTrue(rows["locomo_like_local_proxy"]["can_run"])
        self.assertFalse(rows["locomo_like_local_proxy"]["leaderboard_claim"])
        self.assertEqual("not_run", rows["locomo_like_local_proxy"]["execution"]["status"])
        self.assertEqual("implemented", rows["locomo_external"]["benchmark_adapter_protocol"])
        self.assertEqual("dataset_required", rows["longmemeval_external"]["status"])
        self.assertEqual("implemented", rows["longmemeval_external"]["benchmark_adapter_protocol"])

    def test_longmemeval_adapter_loads_public_shape_into_contract(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_longmemeval_fixture(Path(td) / "longmemeval.json")

            adapter = LongMemEvalAdapter()
            conversations = load_longmemeval_corpus(path)

            self.assertIsInstance(adapter, BenchmarkAdapter)
            self.assertEqual(1, len(conversations))
            conv = conversations[0]
            self.assertEqual("longmemeval", conv.benchmark_name)
            self.assertEqual(4, len(conv.turns))
            self.assertEqual(1, len(conv.qa_cases))
            self.assertEqual(["s2"], conv.qa_cases[0].gold_evidence)
            self.assertIn("single-session-user", conv.qa_cases[0].bucket_labels)
            self.assertEqual(1.0, adapter.score_answer(qa=conv.qa_cases[0], prediction="Northstar"))
            self.assertEqual(
                1.0,
                adapter.score_evidence(qa=conv.qa_cases[0], retrieved_ids=["s2"], k=1)["recall@1"],
            )

    def test_longmemeval_adapter_smoke_and_contrast_run_when_path_supplied(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_longmemeval_fixture(Path(td) / "longmemeval.json")

            smoke = run_adapter_smoke(corpus=path, limit=1)
            self.assertEqual("completed", smoke["status"])
            self.assertFalse(smoke["leaderboard_claim"])
            self.assertEqual(1, smoke["summary"]["conversation_count"])

            eval_smoke = run_evaluation_smoke(corpus=path, limit=1)
            self.assertEqual("completed", eval_smoke["status"])
            self.assertFalse(eval_smoke["leaderboard_claim"])
            self.assertEqual(1, eval_smoke["summary"]["conversation_count"])

            report = build_real_data_contrast(
                longmemeval_corpus=path,
                run_external_adapter_smoke=True,
                run_external_eval_smoke=True,
                external_adapter_limit=1,
                external_eval_limit=1,
            )
            rows = {row["dataset_id"]: row for row in report["datasets"]}

            self.assertEqual("ready_with_external_evaluation", report["status"])
            self.assertEqual("evaluation_smoke_completed", rows["longmemeval_external"]["status"])
            self.assertEqual("completed", rows["longmemeval_external"]["execution"]["status"])
            self.assertEqual(1, report["summary"]["external_adapter_smoke_count"])
            self.assertEqual(1, report["summary"]["external_eval_smoke_count"])
            self.assertEqual(0, report["summary"]["leaderboard_claim_count"])

    def test_suite_report_attaches_real_data_contrast_when_requested(self):
        report = run_suite(
            fixtures_dir=_FIXTURES,
            gold_dir=_GOLD,
            strategies=["bm25"],
            tasks=["t1"],
            subset="local",
            limit=1,
            include_real_data_contrast=True,
        )

        self.assertIn("real_data_contrast", report)
        self.assertEqual(0, report["real_data_contrast"]["summary"]["leaderboard_claim_count"])
        self.assertIn("pr7_real_data_contrast", report["metadata"]["notes"])

        text = render_summary(report)
        self.assertIn("Real-data contrast", text)
        self.assertIn("leaderboard_claims=0", text)

    def test_suite_report_can_smoke_longmemeval_corpus_path(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_longmemeval_fixture(Path(td) / "longmemeval.json")
            report = run_suite(
                fixtures_dir=_FIXTURES,
                gold_dir=_GOLD,
                strategies=["bm25"],
                tasks=["t1"],
                subset="local",
                limit=1,
                include_real_data_contrast=True,
                longmemeval_corpus=path,
                run_real_data_adapter_smoke=True,
                run_real_data_eval_smoke=True,
            )

            rows = {row["dataset_id"]: row for row in report["real_data_contrast"]["datasets"]}
            self.assertEqual("evaluation_smoke_completed", rows["longmemeval_external"]["status"])
            self.assertEqual(1, report["real_data_contrast"]["summary"]["external_eval_smoke_count"])


if __name__ == "__main__":
    unittest.main()
